import enum
import uuid
from datetime import datetime

from sqlalchemy import String, Enum, DateTime, ForeignKey, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ApiKeyStatus(str, enum.Enum):
    CONNECTED = "Connected"
    ERROR = "Error"
    DISCONNECTED = "Disconnected"


class ApiKey(Base):
    """
    The real secret (encrypted_value) is written once and never returned by
    any endpoint — API responses only ever expose `masked`. Swap the
    encryption call in api_keys.py for a KMS/Vault call in production instead
    of the placeholder Fernet key used here.
    """
    __tablename__ = "api_keys"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    provider: Mapped[str] = mapped_column(String(80))
    masked: Mapped[str] = mapped_column(String(60))
    encrypted_value: Mapped[str] = mapped_column(String(500))
    business_account_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    phone_number_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    status: Mapped[ApiKeyStatus] = mapped_column(Enum(ApiKeyStatus, name="api_key_status"), default=ApiKeyStatus.CONNECTED)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
