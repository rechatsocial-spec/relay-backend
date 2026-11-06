"""
Campaign dispatch: resolves the audience, then sends one message per contact
at a safe pace, recording each as a Message in that contact's inbox thread.

Runs in a background thread inside the API process (FastAPI BackgroundTasks),
which is fine for a single server. At larger scale move run_campaign() to a
queue worker (RQ/Celery) — the function itself doesn't need to change.

Pause = PATCH /campaigns/{id} {"status": "Paused"}; the loop checks status
before every send. Re-launching a paused campaign skips contacts that were
already messaged by it.
"""
import logging
import threading
import time
import uuid

from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.campaign import Campaign, CampaignStatus, MessageContentType
from app.models.contact import Contact, ContactStatus
from app.models.conversation import Conversation, Message, MessageType
from app.models.whatsapp_number import WhatsAppNumber
from app.services.messaging import get_or_create_conversation, send_message, utcnow
from app.services.whatsapp_service import get_whatsapp_service

log = logging.getLogger("relay.campaigns")

_running: set[uuid.UUID] = set()
_lock = threading.Lock()


def resolve_audience(db: Session, number: WhatsAppNumber, audience_filter: dict | None) -> tuple[list[Contact], int]:
    """Contacts owned by the number's admin, filtered by status/tag. Returns (recipients, opted_out_skipped)."""
    f = audience_filter or {}
    q = db.query(Contact).filter(Contact.admin_id == number.admin_id)
    if f.get("status"):
        try:
            q = q.filter(Contact.status == ContactStatus(f["status"]))
        except ValueError:
            return [], 0
    contacts = q.order_by(Contact.created_at).all()
    if f.get("tag"):
        tag = f["tag"].strip().lower()
        contacts = [c for c in contacts if any(t.lower() == tag for t in (c.tags or []))]
    recipients = [c for c in contacts if not c.opted_out]
    return recipients, len(contacts) - len(recipients)


def personalize(text: str | None, contact: Contact) -> str | None:
    if not text:
        return text
    first = (contact.name or "").split(" ")[0] or "there"
    return text.replace("{{name}}", first).replace("{{full_name}}", contact.name or "")


_TYPE_MAP = {
    MessageContentType.TEXT: MessageType.TEXT, MessageContentType.IMAGE: MessageType.IMAGE,
    MessageContentType.VIDEO: MessageType.VIDEO, MessageContentType.STICKER: MessageType.STICKER,
    MessageContentType.VOICE: MessageType.VOICE,
}


def run_campaign(campaign_id: uuid.UUID) -> None:
    with _lock:
        if campaign_id in _running:
            return
        _running.add(campaign_id)
    db = SessionLocal()
    try:
        _run(db, campaign_id)
    except Exception:
        log.exception("Campaign %s crashed", campaign_id)
        db.rollback()
        campaign = db.get(Campaign, campaign_id)
        if campaign and campaign.status == CampaignStatus.RUNNING:
            campaign.status = CampaignStatus.FAILED
            campaign.finished_at = utcnow()
            db.commit()
    finally:
        db.close()
        with _lock:
            _running.discard(campaign_id)


def _run(db: Session, campaign_id: uuid.UUID) -> None:
    campaign = db.get(Campaign, campaign_id)
    if not campaign or campaign.status != CampaignStatus.RUNNING:
        return
    number = db.get(WhatsAppNumber, campaign.whatsapp_number_id)
    whatsapp = get_whatsapp_service()
    recipients, _ = resolve_audience(db, number, campaign.audience_filter)

    # contacts this campaign already messaged (resume after pause / restart)
    done_contact_ids = {
        cid for (cid,) in db.query(Conversation.contact_id)
        .join(Message, Message.conversation_id == Conversation.id)
        .filter(Message.campaign_id == campaign.id)
        .distinct()
    }
    msg_type = _TYPE_MAP.get(campaign.message_type, MessageType.TEXT)
    # Real providers need pacing (sandbox ~1 msg / 3 s); mock mode just shows quick progress.
    interval = max(settings.CAMPAIGN_SEND_INTERVAL_SECONDS, 0) if not settings.WHATSAPP_MOCK else 0.2

    for contact in recipients:
        if contact.id in done_contact_ids:
            continue
        db.refresh(campaign)
        if campaign.status != CampaignStatus.RUNNING:
            log.info("Campaign %s stopped (%s)", campaign.id, campaign.status.value)
            return
        db.refresh(contact)
        if contact.opted_out:
            continue

        conv = get_or_create_conversation(db, contact, number)
        msg = send_message(
            db, whatsapp, number=number, contact=contact, conversation=conv,
            msg_type=MessageType.TEXT if campaign.content_sid else msg_type,
            text=personalize(campaign.message_text, contact),
            media_url=campaign.media_url,
            content_sid=campaign.content_sid,
            content_variables={k: personalize(str(v), contact) for k, v in (campaign.content_variables or {}).items()} or None,
            campaign_id=campaign.id,
        )
        if msg.provider_message_id:
            campaign.sent += 1
        else:
            campaign.failed += 1
        db.commit()
        if interval:
            time.sleep(interval)

    db.refresh(campaign)
    if campaign.status == CampaignStatus.RUNNING:
        campaign.status = CampaignStatus.FAILED if campaign.sent == 0 and campaign.failed > 0 else CampaignStatus.COMPLETED
        campaign.finished_at = utcnow()
        db.commit()
        log.info("Campaign %s finished: %s sent, %s failed", campaign.id, campaign.sent, campaign.failed)


def resume_interrupted_campaigns() -> None:
    """Called on startup: campaigns left Running by a restart carry on where they stopped."""
    db = SessionLocal()
    try:
        ids = [c.id for c in db.query(Campaign).filter(
            Campaign.status == CampaignStatus.RUNNING, Campaign.started_at.isnot(None)
        ).all()]
    finally:
        db.close()
    for cid in ids:
        threading.Thread(target=run_campaign, args=(cid,), daemon=True).start()
