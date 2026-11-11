"""Small helpers shared by the webhook handler and the chat importer."""
import re

from sqlalchemy.orm import Session

from app.models.contact import Contact
from app.models.conversation import MessageType
from app.models.whatsapp_number import WhatsAppNumber
from app.services.whatsapp_service import to_e164


def find_contact(db: Session, number: WhatsAppNumber, address: str) -> Contact | None:
    target = to_e164(address)
    candidates = db.query(Contact).filter(Contact.admin_id == number.admin_id).all()
    matches = [c for c in candidates if to_e164(c.phone) == target]
    matches.sort(key=lambda c: c.whatsapp_number_id != number.id)  # same number first
    return matches[0] if matches else None


def message_type(content_type: str | None) -> MessageType:
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


def digits_to_phone(value: str | None) -> str | None:
    digits = re.sub(r"\D", "", value or "")
    return f"+{digits}" if len(digits) >= 7 else None


def phone_from_user_id(user_id: str) -> str | None:
    """'919820011234@s.whatsapp.net' -> '+919820011234'. '@lid' ids hide the number."""
    if not user_id.endswith("@s.whatsapp.net"):
        return None
    digits = re.sub(r"\D", "", user_id.split("@", 1)[0])
    return f"+{digits}" if digits else None
