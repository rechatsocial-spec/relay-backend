import uuid
from datetime import datetime

from pydantic import BaseModel

from app.models.whatsapp_number import NumberStatus


class WhatsAppNumberCreate(BaseModel):
    phone: str
    display_name: str
    waba_id: str | None = None
    phone_number_id: str | None = None
    admin_id: uuid.UUID


class WhatsAppNumberUpdate(BaseModel):
    display_name: str | None = None
    admin_id: uuid.UUID | None = None
    status: NumberStatus | None = None


class WhatsAppNumberOut(BaseModel):
    id: uuid.UUID
    phone: str
    display_name: str
    admin_id: uuid.UUID
    status: NumberStatus
    unipile_account_id: str | None = None
    messages_today: int
    delivery_rate: float
    reply_rate: float
    last_activity_at: datetime | None
    created_at: datetime

    class Config:
        from_attributes = True
