import enum
import uuid
from datetime import datetime

from sqlalchemy import String, Enum, DateTime, ForeignKey, Text, Boolean, func, false
from sqlalchemy.dialects.postgresql import UUID, ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ContactStatus(str, enum.Enum):
    NEW = "New"
    CONTACTED = "Contacted"
    REPLIED = "Replied"
    QUALIFIED = "Qualified"
    DEMO = "Demo"
    CONVERTED = "Converted"
    LOST = "Lost"


class Contact(Base):
    __tablename__ = "contacts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(120))
    phone: Mapped[str] = mapped_column(String(30), index=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    city: Mapped[str | None] = mapped_column(String(80), nullable=True)

    whatsapp_number_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("whatsapp_numbers.id"))
    admin_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), index=True)

    status: Mapped[ContactStatus] = mapped_column(Enum(ContactStatus, name="contact_status"), default=ContactStatus.NEW)
    tags: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Set when the contact replies STOP. Campaigns skip opted-out contacts.
    opted_out: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    # Where the lead came from, e.g. "Click-to-WhatsApp ad: Diwali Sale".
    source: Mapped[str | None] = mapped_column(String(255), nullable=True)

    last_contact_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
