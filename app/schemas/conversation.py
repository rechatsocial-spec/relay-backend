import uuid
from datetime import datetime

from pydantic import BaseModel

from app.models.conversation import ConversationStatus, MessageSender, MessageType, MessageStatus


class MessageOut(BaseModel):
    id: uuid.UUID
    conversation_id: uuid.UUID
    sender: MessageSender
    type: MessageType
    text: str | None
    media_url: str | None
    duration_seconds: int | None
    status: MessageStatus
    error_code: str | None = None
    error_message: str | None = None
    campaign_id: uuid.UUID | None = None
    created_at: datetime

    class Config:
        from_attributes = True


class SendMessageRequest(BaseModel):
    type: MessageType = MessageType.TEXT
    text: str | None = None
    media_url: str | None = None
    duration_seconds: int | None = None
    # Template sends (needed outside the 24h window): unsupported with Unipile — the send is rejected with a clear error
    content_sid: str | None = None
    content_variables: dict | None = None


class ContactSummary(BaseModel):
    """Minimal contact info embedded in conversation responses so the inbox
    doesn't need a second round trip (or a client-side join) just to show a
    name and phone number next to each conversation."""
    id: uuid.UUID
    name: str
    phone: str
    email: str | None = None

    class Config:
        from_attributes = True


class ConversationOut(BaseModel):
    id: uuid.UUID
    contact_id: uuid.UUID
    whatsapp_number_id: uuid.UUID
    admin_id: uuid.UUID
    status: ConversationStatus
    unread_count: int
    last_message_at: datetime
    last_inbound_at: datetime | None = None
    contact: ContactSummary
    whatsapp_number_phone: str
    whatsapp_number_name: str | None = None

    class Config:
        from_attributes = True


class ConversationDetailOut(ConversationOut):
    messages: list[MessageOut]
