import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, joinedload

from app.api.deps import admin_scope_filter
from app.db.session import get_db
from app.models.conversation import Conversation, Message, MessageSender, MessageStatus, MessageType, ConversationStatus
from app.models.contact import Contact
from app.models.whatsapp_number import WhatsAppNumber
from app.services.whatsapp_service import get_whatsapp_service, WhatsAppServiceInterface
from app.services.messaging import send_message as deliver_message
from app.schemas.conversation import ConversationOut, ConversationDetailOut, SendMessageRequest, MessageOut, ContactSummary

router = APIRouter(prefix="/inbox", tags=["inbox"])


def _to_conversation_out(conv: Conversation, contact: Contact, number: WhatsAppNumber) -> ConversationOut:
    return ConversationOut(
        id=conv.id, contact_id=conv.contact_id, whatsapp_number_id=conv.whatsapp_number_id,
        admin_id=conv.admin_id, status=conv.status, unread_count=conv.unread_count,
        last_message_at=conv.last_message_at, last_inbound_at=conv.last_inbound_at,
        contact=ContactSummary(id=contact.id, name=contact.name, phone=contact.phone, email=contact.email),
        whatsapp_number_phone=number.phone,
    )


@router.get("/conversations", response_model=list[ConversationOut])
def list_conversations(
    status_filter: ConversationStatus | None = Query(None, alias="status"),
    db: Session = Depends(get_db),
    scope: uuid.UUID | None = Depends(admin_scope_filter),
):
    q = db.query(Conversation)
    if scope:
        q = q.filter(Conversation.admin_id == scope)
    if status_filter:
        q = q.filter(Conversation.status == status_filter)
    conversations = q.order_by(Conversation.last_message_at.desc()).all()

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
