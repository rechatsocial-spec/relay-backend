import enum
import uuid
from datetime import datetime

from sqlalchemy import String, Enum, DateTime, ForeignKey, Integer, Float, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID

from app.db.base import Base


class NumberStatus(str, enum.Enum):
    ACTIVE = "active"
    WARNING = "warning"
    ERROR = "error"
    INACTIVE = "inactive"


class WhatsAppNumber(Base):
    __tablename__ = "whatsapp_numbers"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    phone: Mapped[str] = mapped_column(String(30), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(120))
    waba_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    phone_number_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    # Unipile account id ("acc_…") of the linked WhatsApp session, if any.
    unipile_account_id: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    # Set once the existing chats of this account have been imported.
    history_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    admin_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    status: Mapped[NumberStatus] = mapped_column(Enum(NumberStatus, name="number_status"), default=NumberStatus.ACTIVE)

    # Rolling counters — in production these are updated by webhook events /
    # a nightly aggregation job, not computed on read.
    messages_today: Mapped[int] = mapped_column(Integer, default=0)
    delivery_rate: Mapped[float] = mapped_column(Float, default=0.0)
    reply_rate: Mapped[float] = mapped_column(Float, default=0.0)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_activity_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    admin = relationship("User", back_populates="whatsapp_numbers", foreign_keys=[admin_id])
