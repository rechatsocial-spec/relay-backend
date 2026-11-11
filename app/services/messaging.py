"""
One place that turns "send X to this contact" into a provider call + a stored
Message row. Used by the inbox (one message) and campaigns (many).
"""
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.models.contact import Contact
from app.models.conversation import (
    Conversation, ConversationStatus, Message, MessageSender, MessageStatus, MessageType,
)
from app.models.whatsapp_number import WhatsAppNumber
from app.services.whatsapp_service import SendResult, WhatsAppServiceInterface

SERVICE_WINDOW = timedelta(hours=24)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def in_service_window(conv: Conversation | None) -> bool:
    """True if the customer wrote in the last 24h (free-form messages allowed)."""
    if not conv or not conv.last_inbound_at:
        return False
    last = conv.last_inbound_at
    if last.tzinfo is None:  # SQLite returns naive datetimes
        last = last.replace(tzinfo=timezone.utc)
    return utcnow() - last < SERVICE_WINDOW


def get_or_create_conversation(db: Session, contact: Contact, number: WhatsAppNumber) -> Conversation:
    conv = (
        db.query(Conversation)
        .filter(Conversation.contact_id == contact.id, Conversation.whatsapp_number_id == number.id)
        .order_by(Conversation.last_message_at.desc())
        .first()
    )
    if conv:
        return conv
    conv = Conversation(
        contact_id=contact.id, whatsapp_number_id=number.id, admin_id=number.admin_id,
        status=ConversationStatus.HUMAN, unread_count=0, last_message_at=utcnow(),
    )
    db.add(conv)
    db.flush()
    return conv


def send_message(
    db: Session,
    whatsapp: WhatsAppServiceInterface,
    *,
    number: WhatsAppNumber,
    contact: Contact,
    conversation: Conversation,
    msg_type: MessageType = MessageType.TEXT,
    text: str | None = None,
    media_url: str | None = None,
    duration_seconds: int | None = None,
    content_sid: str | None = None,
    content_variables: dict | None = None,
    campaign_id: uuid.UUID | None = None,
) -> Message:
    """Sends via the provider and records the result. Does not commit."""
    if content_sid:
        result = whatsapp.send_template(number.phone, contact.phone, content_sid, content_variables)
    elif msg_type == MessageType.TEXT:
        result = whatsapp.send_text(number.phone, contact.phone, text or "")
    elif media_url:
        result = whatsapp.send_media(number.phone, contact.phone, media_url, caption=text)
    else:
        result = SendResult(False, None, error="A media URL is required for this message type")

    message = Message(
        conversation_id=conversation.id, sender=MessageSender.BUSINESS, type=msg_type,
        text=text, media_url=media_url, duration_seconds=duration_seconds,
        status=MessageStatus.SENT if result.success else MessageStatus.FAILED,
        provider_message_id=result.provider_message_id,
        error_code=result.error_code, error_message=result.error,
        campaign_id=campaign_id,
    )
    db.add(message)
    now = utcnow()
    conversation.last_message_at = now
    contact.last_contact_at = now
    number.last_activity_at = now
    if result.success:
        number.messages_today = (number.messages_today or 0) + 1
    return message


def send_attachment_message(
    db: Session, whatsapp: WhatsAppServiceInterface, *, number: WhatsAppNumber, contact: Contact, conversation: Conversation,
    data: bytes, content_type: str, filename: str, caption: str | None, voice_note: bool, duration: int | None, media_url: str,
) -> Message:
    """Sends a file through the provider and records it. Does not commit."""
    result = whatsapp.send_attachment(number.phone, contact.phone, data, content_type, filename,
                                      caption=caption or None, voice_note=voice_note, duration=duration)
    if content_type.startswith("image/"):
        mtype = MessageType.IMAGE
    elif content_type.startswith("video/"):
        mtype = MessageType.VIDEO
    elif content_type.startswith("audio/"):
        mtype = MessageType.VOICE
    else:
        mtype = MessageType.DOCUMENT
    message = Message(
        conversation_id=conversation.id, sender=MessageSender.BUSINESS, type=mtype, text=caption or None, media_url=media_url,
        media_name=filename[:255], media_type=content_type[:120], duration_seconds=duration if voice_note else None,
        status=MessageStatus.SENT if result.success else MessageStatus.FAILED,
        provider_message_id=result.provider_message_id, error_code=result.error_code, error_message=result.error,
    )
    db.add(message)
    now = utcnow()
    conversation.last_message_at = now
    contact.last_contact_at = now
    number.last_activity_at = now
    return message
