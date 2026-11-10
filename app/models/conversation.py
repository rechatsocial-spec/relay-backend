import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import String, Enum, DateTime, ForeignKey, Text, Integer, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class ConversationStatus(str, enum.Enum):
    BOT = "bot"
    NEEDS_HUMAN = "needs_human"
    HUMAN = "human"


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    contact_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("contacts.id"), index=True)
    whatsapp_number_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("whatsapp_numbers.id"))
    admin_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), index=True)
    status: Mapped[ConversationStatus] = mapped_column(Enum(ConversationStatus, name="conversation_status"), default=ConversationStatus.BOT)
    unread_count: Mapped[int] = mapped_column(Integer, default=0)
    last_message_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # Last time the *customer* wrote. WhatsApp only allows free-form replies
    # within 24h of this; after that you must send an approved template.
    last_inbound_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Unipile chat id exactly as returned (may end in @lid) — replies are sent to it.
    provider_chat_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    messages = relationship("Message", back_populates="conversation", order_by="Message.created_at")


class MessageSender(str, enum.Enum):
    CUSTOMER = "customer"
    BOT = "bot"
    BUSINESS = "business"
    SYSTEM = "system"


class MessageType(str, enum.Enum):
    TEXT = "text"
    IMAGE = "image"
    VIDEO = "video"
    STICKER = "sticker"
    VOICE = "voice"
    NOTE = "note"


class MessageStatus(str, enum.Enum):
    QUEUED = "queued"
    SENT = "sent"
    DELIVERED = "delivered"
    READ = "read"
    FAILED = "failed"
    UNDELIVERED = "undelivered"


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("conversations.id"), index=True)
    sender: Mapped[MessageSender] = mapped_column(Enum(MessageSender, name="message_sender"))
    type: Mapped[MessageType] = mapped_column(Enum(MessageType, name="message_type"), default=MessageType.TEXT)
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    media_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[MessageStatus] = mapped_column(Enum(MessageStatus, name="message_status"), default=MessageStatus.SENT)
    # Provider tracking — status webhooks find the message by provider_message_id.
    provider_message_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    error_code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(300), nullable=True)
    campaign_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("campaigns.id"), nullable=True, index=True)
    # Set in Python (microsecond precision) so messages created in the same
    # second still sort in the order they happened. server_default is a fallback.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), server_default=func.now(), index=True
    )

    conversation = relationship("Conversation", back_populates="messages")
