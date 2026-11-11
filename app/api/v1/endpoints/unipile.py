"""
WhatsApp webhook receiver (messages, receipts, account status).

Numbers are not linked from this app: every WhatsApp account connected at the
provider is imported automatically (app/services/sync.py), together with its
recent chats. This module only handles the live events afterwards.

Webhook address (register it once at the provider):
  {PUBLIC_BASE_URL}/api/v1/whatsapp/webhook
Events: message.new, message.receipt.delivery, message.receipt.read,
        account.status.*, account.remove
Requests carry `unipile-signature: t=<unix>,v0=<hmac_sha256(secret, "t.rawbody")>`;
the secret is UNIPILE_WEBHOOK_SECRET.
"""
import hashlib
import hmac
import logging
import time
import uuid

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import get_db
from app.models.activity_log import ActivityLog
from app.models.campaign import Campaign
from app.models.contact import Contact, ContactStatus
from app.models.conversation import Conversation, ConversationStatus, Message, MessageSender, MessageStatus, MessageType
from app.models.whatsapp_number import NumberStatus, WhatsAppNumber
from app.services.automation_runner import start_keyword_automations, start_new_customer_automations
from app.services.chat_utils import attachment_fields, digits_to_phone, find_contact, is_person_chat, lid_placeholder, phone_from_user_id
from app.services.messaging import get_or_create_conversation, utcnow

log = logging.getLogger("relay.unipile")
router = APIRouter(tags=["whatsapp"])  # mounted at /whatsapp (and legacy /unipile) in api.py

STOP_WORDS = {"STOP", "STOPALL", "UNSUBSCRIBE", "CANCEL", "END", "QUIT"}
START_WORDS = {"START", "UNSTOP", "SUBSCRIBE"}

# Receipts can arrive out of order; never move a message "backwards".
STATUS_RANK = {
    MessageStatus.QUEUED: 0, MessageStatus.SENT: 1, MessageStatus.DELIVERED: 2,
    MessageStatus.UNDELIVERED: 3, MessageStatus.FAILED: 3, MessageStatus.READ: 4,
}


SIGNATURE_TOLERANCE_SECONDS = 300
ACCOUNT_STATUS = {
    "running": NumberStatus.ACTIVE,
    "degraded": NumberStatus.WARNING,
    "partial": NumberStatus.WARNING,
    "errored": NumberStatus.ERROR,
    "disconnected": NumberStatus.ERROR,
}


def _headers() -> dict:
    if not settings.UNIPILE_API_KEY:
        raise HTTPException(503, "UNIPILE_API_KEY is not configured")
    return {"X-API-KEY": settings.UNIPILE_API_KEY, "accept": "application/json"}


def _api(path: str) -> str:
    return settings.UNIPILE_BASE_URL.rstrip("/") + path


# ── Webhook ──────────────────────────────────────────────────────────────────

def _verify_signature(raw: bytes, header: str) -> None:
    if not settings.UNIPILE_WEBHOOK_SECRET:
        raise HTTPException(503, "UNIPILE_WEBHOOK_SECRET is not configured")
    parts = dict(p.split("=", 1) for p in header.split(",") if "=" in p)
    t, sig = parts.get("t", ""), parts.get("v0", "")
    if not t.isdigit() or abs(time.time() - int(t)) > SIGNATURE_TOLERANCE_SECONDS:
        raise HTTPException(403, "Stale or missing signature timestamp")
    expected = hmac.new(settings.UNIPILE_WEBHOOK_SECRET.encode(), f"{t}.".encode() + raw, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig):
        log.warning("Rejected Unipile webhook with bad signature")
        raise HTTPException(403, "Invalid webhook signature")


def _resolve_phone(number: WhatsAppNumber, m: dict) -> str | None:
    """Phone of the sender. WhatsApp may identify people by an @lid id; then the
    phone is in sender.public_identifier, or in the user profile (GET /{account}/users/{id})."""
    sender_id = m.get("sender_id") or ""
    phone = phone_from_user_id(sender_id)
    if phone:
        return phone
    phone = digits_to_phone((m.get("sender") or {}).get("public_identifier"))
    if phone or not sender_id or not number.unipile_account_id:
        return phone
    try:
        r = httpx.get(_api(f"/{number.unipile_account_id}/users/{sender_id}"), headers=_headers(), timeout=15)
        if r.status_code < 300:
            return digits_to_phone(r.json().get("public_identifier"))
    except (httpx.HTTPError, HTTPException, ValueError):
        log.warning("Could not look up profile for %s", sender_id)
    return None


def _handle_message_new(db: Session, number: WhatsAppNumber, m: dict) -> None:
    if m.get("is_sender") or m.get("is_event"):
        return  # our own message (sent via the API or the phone) / a system event
    mid = m.get("id")
    if mid and db.query(Message).filter(Message.provider_message_id == mid).first():
        return  # webhook retry
    chat_id = m.get("chat_id") or ""
    if not is_person_chat(chat_id):
        return  # groups, status, newsletters, WhatsApp's own system chat: not CRM customers
    sender_id = m.get("sender_id") or chat_id
    conv = db.query(Conversation).filter(Conversation.provider_chat_id == chat_id, Conversation.whatsapp_number_id == number.id).first()
    is_new_customer = False
    if conv is not None:
        contact = db.get(Contact, conv.contact_id)
    else:
        phone = _resolve_phone(number, m)  # None if WhatsApp hides it: the chat id then stands in for it
        key = phone or lid_placeholder(sender_id)
        name = ((m.get("sender") or {}).get("display_name") or phone or "WhatsApp contact")[:120]
        contact = find_contact(db, number, key)
        is_new_customer = contact is None
        if not contact:
            contact = Contact(
                name=name, phone=key, whatsapp_number_id=number.id, admin_id=number.admin_id,
                status=ContactStatus.NEW, tags=["WhatsApp inbound"], source="Inbound WhatsApp",
            )
            db.add(contact)
            db.flush()
        conv = get_or_create_conversation(db, contact, number)
    conv.provider_chat_id = chat_id  # reply to the id Unipile uses, unchanged (may be @lid)
    previous = (
        db.query(Message).filter(Message.conversation_id == conv.id)
        .order_by(Message.created_at.desc()).first()
    )

    attachments = m.get("attachments") or []
    first = attachments[0] if attachments else None
    body = m.get("text") or None
    mid_local = uuid.uuid4()
    fields = {"type": MessageType.TEXT, **attachment_fields(first, mid_local)}
    db.add(Message(
        id=mid_local, conversation_id=conv.id, sender=MessageSender.CUSTOMER, text=body,
        status=MessageStatus.DELIVERED, provider_message_id=mid, **fields,
    ))

    keyword = (body or "").strip().upper()
    if keyword in STOP_WORDS and not contact.opted_out:
        contact.opted_out = True
        db.add(Message(conversation_id=conv.id, sender=MessageSender.SYSTEM, type=MessageType.NOTE,
                       text="Contact opted out (STOP) — campaigns will skip them", status=MessageStatus.SENT))
    elif keyword in START_WORDS and contact.opted_out:
        contact.opted_out = False
        db.add(Message(conversation_id=conv.id, sender=MessageSender.SYSTEM, type=MessageType.NOTE,
                       text="Contact opted back in (START)", status=MessageStatus.SENT))

    if previous and previous.sender == MessageSender.BUSINESS and previous.campaign_id:
        campaign = db.get(Campaign, previous.campaign_id)
        if campaign:
            campaign.replies += 1

    now = utcnow()
    if contact.status in (ContactStatus.NEW, ContactStatus.CONTACTED):
        contact.status = ContactStatus.REPLIED
    contact.last_contact_at = now
    conv.last_inbound_at = now
    conv.last_message_at = now
    conv.unread_count = (conv.unread_count or 0) + 1
    if conv.status in (ConversationStatus.BOT, ConversationStatus.HUMAN):
        conv.status = ConversationStatus.NEEDS_HUMAN
    number.last_activity_at = now
    if is_new_customer and not contact.opted_out:
        start_new_customer_automations(db, number, contact, conv)  # sent by the poller within seconds
    if not contact.opted_out and keyword not in STOP_WORDS:
        start_keyword_automations(db, number, contact, conv, body or "")  # the message may contain a trigger keyword
    db.commit()


def _handle_receipt(db: Session, message_id: str | None, new: MessageStatus) -> None:
    message = db.query(Message).filter(Message.provider_message_id == message_id).first() if message_id else None
    if not message:
        return
    old = message.status
    if STATUS_RANK.get(new, 0) <= STATUS_RANK.get(old, 0):
        return
    message.status = new
    if message.campaign_id:
        campaign = db.get(Campaign, message.campaign_id)
        if campaign:
            if new in (MessageStatus.DELIVERED, MessageStatus.READ) and old not in (MessageStatus.DELIVERED, MessageStatus.READ):
                campaign.delivered += 1
            if new == MessageStatus.READ:
                campaign.read += 1
    db.commit()


@router.post("/webhook", status_code=200)
async def unipile_webhook(request: Request, db: Session = Depends(get_db)):
    raw = await request.body()
    _verify_signature(raw, request.headers.get("unipile-signature", ""))
    try:
        event = await request.json()
    except Exception:
        raise HTTPException(400, "Invalid JSON")

    etype = event.get("type", "")
    payload = event.get("payload") or {}
    account_id = event.get("account_id")
    number = db.query(WhatsAppNumber).filter(WhatsAppNumber.unipile_account_id == account_id).first() if account_id else None

    if not number:
        log.info("Unipile event %s for unknown account %s", etype, account_id)
        return Response(status_code=200)

    if etype == "message.new":
        _handle_message_new(db, number, payload)
    elif etype == "message.receipt.delivery":
        _handle_receipt(db, payload.get("message_id"), MessageStatus.DELIVERED)
    elif etype == "message.receipt.read":
        _handle_receipt(db, payload.get("message_id"), MessageStatus.READ)
    elif etype.startswith("account.status."):
        new = ACCOUNT_STATUS.get(etype.rsplit(".", 1)[-1])
        if new and number.status != new:
            number.status = new
            db.add(ActivityLog(type="whatsapp_number", message=f"WhatsApp number {number.phone} is now {new.value}"))
            db.commit()
    elif etype == "account.remove":
        number.status = NumberStatus.INACTIVE
        db.commit()
    return Response(status_code=200)
