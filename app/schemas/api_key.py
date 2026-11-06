import uuid
from datetime import datetime

from pydantic import BaseModel

from app.models.api_key import ApiKeyStatus


class ApiKeyCreate(BaseModel):
    provider: str
    api_key: str          # raw value — never stored or returned as-is
    api_secret: str | None = None
    business_account_id: str | None = None
    phone_number_id: str | None = None


class ApiKeyOut(BaseModel):
    id: uuid.UUID
    provider: str
    masked: str            # e.g. "•••••••••••••••8F29" — the only form ever returned
    business_account_id: str | None
    phone_number_id: str | None
    status: ApiKeyStatus
    updated_at: datetime

    class Config:
        from_attributes = True


class ActivityLogOut(BaseModel):
    id: uuid.UUID
    actor_id: uuid.UUID | None
    type: str
    message: str
    created_at: datetime

    class Config:
        from_attributes = True
