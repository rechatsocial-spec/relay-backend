import uuid

import mimetypes

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from sqlalchemy.orm import Session, joinedload

from app.api.deps import admin_scope_filter
from app.db.session import get_db
from app.models.conversation import Conversation, Message, MessageSender, MessageStatus, MessageType, ConversationStatus
from app.models.contact import Contact
from app.models.whatsapp_number import WhatsAppNumber
from app.services.whatsapp_service import get_whatsapp_service, WhatsAppServiceInterface
from app.core.config import settings
from app.services.audio import to_ogg_opus
from app.services.media import stored_url
from app.models.media import MediaFile
from app.services.messaging import send_attachment_message, send_message as deliver_message
from app.schemas.conversation import ConversationOut, ConversationDetailOut, SendMessageRequest, MessageOut, ContactSummary

router = APIRouter(prefix="/inbox", tags=["inbox"])


def _to_conversation_out(conv: Conversation, contact: Contact, number: WhatsAppNumber) -> ConversationOut:
    return ConversationOut(
        id=conv.id, contact_id=conv.contact_id, whatsapp_number_id=conv.whatsapp_number_id,
        admin_id=conv.admin_id, status=conv.status, unread_count=conv.unread_count,
        last_message_at=conv.last_message_at, last_inbound_at=conv.last_inbound_at,
        contact=ContactSummary(id=contact.id, name=contact.name, phone=contact.phone, email=contact.email),
        whatsapp_number_phone=number.phone, whatsapp_number_name=number.display_name,
    )


@router.get("/conversations", response_model=list[ConversationOut])
def list_conversations(
    status_filter: ConversationStatus | None = Query(None, alias="status"),
    number_id: uuid.UUID | None = Query(None),
    limit: int = Query(500, ge=1, le=2000),
    db: Session = Depends(get_db),
    scope: uuid.UUID | None = Depends(admin_scope_filter),
):
    q = db.query(Conversation)
    if scope:
        q = q.filter(Conversation.admin_id == scope)
    if status_filter:
        q = q.filter(Conversation.status == status_filter)
    if number_id:
        q = q.filter(Conversation.whatsapp_number_id == number_id)
    conversations = q.order_by(Conversation.last_message_at.desc()).limit(limit).all()

    if not conversations:
        return []
    contact_ids = {c.contact_id for c in conversations}
    number_ids = {c.whatsapp_number_id for c in conversations}
    contacts = {c.id: c for c in db.query(Contact).filter(Contact.id.in_(contact_ids)).all()}
    numbers = {n.id: n for n in db.query(WhatsAppNumber).filter(WhatsAppNumber.id.in_(number_ids)).all()}

    return [_to_conversation_out(c, contacts[c.contact_id], numbers[c.whatsapp_number_id]) for c in conversations]


@router.get("/conversations/{conversation_id}", response_model=ConversationDetailOut)
def get_conversation(conversation_id: uuid.UUID, db: Session = Depends(get_db), scope: uuid.UUID | None = Depends(admin_scope_filter)):
    conv = db.query(Conversation).options(joinedload(Conversation.messages)).filter(Conversation.id == conversation_id).first()
    if not conv or (scope and conv.admin_id != scope):
        raise HTTPException(404, "Conversation not found")
    contact = db.get(Contact, conv.contact_id)
    number = db.get(WhatsAppNumber, conv.whatsapp_number_id)
    conv.unread_count = 0
    db.commit()

    base = _to_conversation_out(conv, contact, number)
    return ConversationDetailOut(**base.model_dump(), messages=[MessageOut.model_validate(m) for m in conv.messages])


@router.post("/conversations/{conversation_id}/messages", response_model=MessageOut)
def send_message(
    conversation_id: uuid.UUID, payload: SendMessageRequest,
    db: Session = Depends(get_db), scope: uuid.UUID | None = Depends(admin_scope_filter),
    whatsapp: WhatsAppServiceInterface = Depends(get_whatsapp_service),
):
    conv = db.get(Conversation, conversation_id)
    if not conv or (scope and conv.admin_id != scope):
        raise HTTPException(404, "Conversation not found")
    contact = db.get(Contact, conv.contact_id)

    # Internal notes are stored on the thread but never sent to the customer.
    if payload.type == MessageType.NOTE:
        message = Message(
            conversation_id=conversation_id, sender=MessageSender.SYSTEM, type=MessageType.NOTE,
            text=payload.text, status=MessageStatus.SENT,
        )
        db.add(message)
        db.commit()
        db.refresh(message)
        return message

    if payload.type == MessageType.TEXT and not (payload.text or "").strip() and not payload.content_sid:
        raise HTTPException(400, "Message text is empty")
    if contact.opted_out:
        raise HTTPException(400, f"{contact.name} replied STOP and has opted out of WhatsApp messages")

    number = db.get(WhatsAppNumber, conv.whatsapp_number_id)
    message = deliver_message(
        db, whatsapp, number=number, contact=contact, conversation=conv,
        msg_type=payload.type, text=payload.text, media_url=payload.media_url,
        duration_seconds=payload.duration_seconds,
        content_sid=payload.content_sid, content_variables=payload.content_variables,
    )
    conv.status = ConversationStatus.HUMAN
    db.commit()
    db.refresh(message)
    return message


@router.post("/conversations/{conversation_id}/attachments", response_model=MessageOut)
def send_attachment(
    conversation_id: uuid.UUID,
    file: UploadFile = File(...),
    caption: str = Form(""),
    voice: bool = Form(False),
    duration: int | None = Form(None),
    db: Session = Depends(get_db), scope: uuid.UUID | None = Depends(admin_scope_filter),
    whatsapp: WhatsAppServiceInterface = Depends(get_whatsapp_service),
):
    """Send a photo / video / document / voice note picked or recorded in the inbox."""
    conv = db.get(Conversation, conversation_id)
    if not conv or (scope and conv.admin_id != scope):
        raise HTTPException(404, "Conversation not found")
    contact = db.get(Contact, conv.contact_id)
    if contact.opted_out:
        raise HTTPException(400, f"{contact.name} replied STOP and has opted out of WhatsApp messages")

    limit = settings.MAX_UPLOAD_MB * 1024 * 1024
    data = file.file.read(limit + 1)
    if not data:
        raise HTTPException(400, "The file is empty")
    if len(data) > limit:
        raise HTTPException(413, f"The file is larger than {settings.MAX_UPLOAD_MB} MB")
    filename = (file.filename or "file").replace("/", "_").replace("\\", "_")[:200]
    content_type = (file.content_type or mimetypes.guess_type(filename)[0] or "application/octet-stream").split(";")[0].strip().lower()

    if voice:
        if content_type not in ("audio/ogg", "audio/opus"):
            converted = to_ogg_opus(data)  # WhatsApp voice notes are Ogg/Opus
            if converted:
                data, content_type, filename = converted, "audio/ogg", filename.rsplit(".", 1)[0] + ".ogg"
        if not content_type.startswith("audio/"):
            raise HTTPException(400, "A voice note must be audio")

    stored = MediaFile(filename=filename, content_type=content_type, size=len(data), data=data)
    db.add(stored)
    db.flush()
    number = db.get(WhatsAppNumber, conv.whatsapp_number_id)
    message = send_attachment_message(
        db, whatsapp, number=number, contact=contact, conversation=conv, data=data, content_type=content_type,
        filename=filename, caption=caption.strip() or None, voice_note=voice, duration=duration, media_url=stored_url(stored.id),
    )
    conv.status = ConversationStatus.HUMAN
    db.commit()
    db.refresh(message)
    return message
