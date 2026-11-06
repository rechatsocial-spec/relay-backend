import enum
import uuid
from datetime import datetime

from sqlalchemy import String, Enum, DateTime, ForeignKey, Integer, JSON, func
from sqlalchemy.dialects.postgresql import UUID, ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ChatbotStatus(str, enum.Enum):
    ACTIVE = "active"
    PAUSED = "paused"


class Chatbot(Base):
    __tablename__ = "chatbots"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(120))
    whatsapp_number_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("whatsapp_numbers.id"))
    admin_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), index=True)

    # Same idea as Automation.flow — the flow builder reads/writes this JSON
    # tree directly (start -> welcome -> menu -> branches).
    flow: Mapped[dict] = mapped_column(JSON, default=dict)
    keywords: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)

    status: Mapped[ChatbotStatus] = mapped_column(Enum(ChatbotStatus, name="chatbot_status"), default=ChatbotStatus.ACTIVE)
    conversations: Mapped[int] = mapped_column(Integer, default=0)
    automated_responses: Mapped[int] = mapped_column(Integer, default=0)
    human_handoffs: Mapped[int] = mapped_column(Integer, default=0)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
