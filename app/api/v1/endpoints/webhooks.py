"""
Twilio webhooks — Twilio calls these, not the frontend. Configure in the
Twilio Console (WhatsApp sandbox settings / your WhatsApp sender):
  "When a message comes in":  {PUBLIC_BASE_URL}/api/v1/webhooks/twilio/inbound   (POST)
  "Status callback URL":      {PUBLIC_BASE_URL}/api/v1/webhooks/twilio/status    (POST)
Outbound sends also pass the status URL per message, so it works even if the
sender-level setting is empty.

Every request must carry a valid X-Twilio-Signature (HMAC of the URL + params
with your Auth Token), so nobody else can inject fake messages.
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import get_db
from app.models.activity_log import ActivityLog
from app.models.campaign import Campaign
from app.models.contact import Contact, ContactStatus
from app.models.conversation import (
    Conversation, ConversationStatus, Message, MessageSender, MessageStatus, MessageType,
)
from app.models.whatsapp_number import WhatsAppNumber
from app.services.messaging import get_or_create_conversation, utcnow
from app.services.whatsapp_service import to_e164

log = logging.getLogger("relay.webhooks")
router = APIRouter(prefix="/webhooks/twilio", tags=["webhooks"])

EMPTY_TWIML = '<?xml version="1.0" encoding="UTF-8"?><Response></Response>'
STOP_WORDS = {"STOP", "STOPALL", "UNSUBSCRIBE", "CANCEL", "END", "QUIT"}
START_WORDS = {"START", "UNSTOP", "SUBSCRIBE"}

# Callbacks can arrive out of order; never move a message "backwards".
STATUS_RANK = {
    MessageStatus.QUEUED: 0, MessageStatus.SENT: 1, MessageStatus.DELIVERED: 2,
    MessageStatus.UNDELIVERED: 3, MessageStatus.FAILED: 3, MessageStatus.READ: 4,
}
TWILIO_STATUS = {
    "accepted": MessageStatus.QUEUED, "queued": MessageStatus.QUEUED, "sending": MessageStatus.QUEUED,
    "sent": MessageStatus.SENT, "delivered": MessageStatus.DELIVERED, "read": MessageStatus.READ,
    "undelivered": MessageStatus.UNDELIVERED, "failed": MessageStatus.FAILED,
}


async def twilio_params(request: Request) -> dict:
    """Parses the form body and rejects requests not signed by Twilio."""
    if not settings.TWILIO_AUTH_TOKEN:
        raise HTTPException(503, "TWILIO_AUTH_TOKEN is not configured")
    from twilio.request_validator import RequestValidator

    params = dict(await request.form())
    # Twilio signs the exact public URL it called. Behind a tunnel/proxy the
    # server sees a different host, so rebuild it from PUBLIC_BASE_URL.
    if settings.PUBLIC_BASE_URL:
        url = settings.PUBLIC_BASE_URL.rstrip("/") + request.url.path
        if request.url.query:
            url += "?" + request.url.query
    else:
        url = str(request.url)
    signature = request.headers.get("X-Twilio-Signature", "")
    if not RequestValidator(settings.TWILIO_AUTH_TOKEN).validate(url, params, signature):
        log.warning("Rejected webhook with bad signature for %s", url)
        raise HTTPException(403, "Invalid Twilio signature")
    return params


def _find_number(db: Session, address: str) -> WhatsAppNumber | None:
    target = to_e164(address)
    for n in db.query(WhatsAppNumber).all():
        if to_e164(n.phone) == target:
            return n
    return None


def _find_contact(db: Session, number: WhatsAppNumber, address: str) -> Contact | None:
    target = to_e164(address)
    candidates = db.query(Contact).filter(Contact.admin_id == number.admin_id).all()
    matches = [c for c in candidates if to_e164(c.phone) == target]
    matches.sort(key=lambda c: c.whatsapp_number_id != number.id)  # same number first
    return matches[0] if matches else None


def _message_type(content_type: str | None) -> MessageType:
    ct = (content_type or "").lower()
    if ct == "image/webp":
        return MessageType.STICKER
    if ct.startswith("image/"):
        return MessageType.IMAGE
    if ct.startswith("video/"):
        return MessageType.VIDEO
    if ct.startswith("audio/"):
        return MessageType.VOICE
    return MessageType.TEXT


def _ad_referral(p: dict) -> str | None:
    """Click-to-WhatsApp ad info Twilio attaches to the first message from an ad."""
    if not any(k.startswith("Referral") for k in p):
        return None
    label = p.get("ReferralHeadline") or p.get("ReferralBody") or p.get("ReferralSourceUrl") or p.get("ReferralSourceId") or "unknown ad"
    return f"Click-to-WhatsApp ad: {label}"[:255]


@router.post("/inbound")
def inbound(params: dict = Depends(twilio_params), db: Session = Depends(get_db)):
    sid = params.get("MessageSid") or params.get("SmsMessageSid")
    if sid and db.query(Message).filter(Message.provider_message_id == sid).first():
        return Response(EMPTY_TWIML, media_type="text/xml")  # Twilio retry — already stored

    number = _find_number(db, params.get("To", ""))
    if not number:
        log.warning("Inbound message to %s, which is not a WhatsApp Number in the CRM", params.get("To"))
        db.add(ActivityLog(type="whatsapp_number", message=f"Ignored message to unknown number {params.get('To')} — add it under WhatsApp Numbers"))
        db.commit()
        return Response(EMPTY_TWIML, media_type="text/xml")

    now = utcnow()
    sender = to_e164(params.get("From", ""))
    referral = _ad_referral(params)
    contact = _find_contact(db, number, sender)
    if not contact:
        contact = Contact(
            name=(params.get("ProfileName") or sender)[:120], phone=sender,
            whatsapp_number_id=number.id, admin_id=number.admin_id,
            status=ContactStatus.NEW, tags=["From ad"] if referral else ["WhatsApp inbound"],
            source=referral or "Inbound WhatsApp",
        )
        db.add(contact)
        db.flush()
    elif referral and not contact.source:
        contact.source = referral

    conv = get_or_create_conversation(db, contact, number)
    previous = (
        db.query(Message).filter(Message.conversation_id == conv.id)
        .order_by(Message.created_at.desc()).first()
    )

    if referral:
        db.add(Message(conversation_id=conv.id, sender=MessageSender.SYSTEM, type=MessageType.NOTE,
                       text=f"Lead came from {referral}", status=MessageStatus.SENT))

    num_media = int(params.get("NumMedia") or 0)
    body = params.get("Body") or params.get("ButtonText") or None
    db.add(Message(
        conversation_id=conv.id, sender=MessageSender.CUSTOMER,
        type=_message_type(params.get("MediaContentType0")) if num_media else MessageType.TEXT,
        text=body, media_url=params.get("MediaUrl0") if num_media else None,
        status=MessageStatus.DELIVERED, provider_message_id=sid,
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

    # A reply to a campaign message counts towards that campaign's replies.
    if previous and previous.sender == MessageSender.BUSINESS and previous.campaign_id:
        campaign = db.get(Campaign, previous.campaign_id)
        if campaign:
            campaign.replies += 1

    if contact.status in (ContactStatus.NEW, ContactStatus.CONTACTED):
        contact.status = ContactStatus.REPLIED
    contact.last_contact_at = now
    conv.last_inbound_at = now
    conv.last_message_at = now
    conv.unread_count = (conv.unread_count or 0) + 1
    if conv.status == ConversationStatus.BOT or conv.status == ConversationStatus.HUMAN:
        conv.status = ConversationStatus.NEEDS_HUMAN
    number.last_activity_at = now
    db.commit()
    return Response(EMPTY_TWIML, media_type="text/xml")


@router.post("/status", status_code=204)
def status_callback(params: dict = Depends(twilio_params), db: Session = Depends(get_db)):
    sid = params.get("MessageSid")
    new = TWILIO_STATUS.get((params.get("MessageStatus") or "").lower())
    message = db.query(Message).filter(Message.provider_message_id == sid).first() if sid else None
    if not message or not new:
        return Response(status_code=204)

    old = message.status
    if STATUS_RANK.get(new, 0) <= STATUS_RANK.get(old, 0):
        return Response(status_code=204)

    message.status = new
    if new in (MessageStatus.FAILED, MessageStatus.UNDELIVERED):
        message.error_code = params.get("ErrorCode") or message.error_code
        message.error_message = (params.get("ErrorMessage") or message.error_message or "")[:300] or None

    if message.campaign_id:
        campaign = db.get(Campaign, message.campaign_id)
        if campaign:
            was_delivered = old in (MessageStatus.DELIVERED, MessageStatus.READ)
            if new in (MessageStatus.DELIVERED, MessageStatus.READ) and not was_delivered:
                campaign.delivered += 1
            if new == MessageStatus.READ:
                campaign.read += 1
            if new in (MessageStatus.FAILED, MessageStatus.UNDELIVERED):
                campaign.failed += 1
                campaign.sent = max(campaign.sent - 1, 0)
    db.commit()
    return Response(status_code=204)

