import uuid
from datetime import datetime

from pydantic import BaseModel

from app.models.automation import AutomationStatus


class AutomationCreate(BaseModel):
    name: str
    trigger_type: str = "new_customer"
    flow: dict = {}


class AutomationUpdate(BaseModel):
    name: str | None = None
    flow: dict | None = None
    status: AutomationStatus | None = None


class AutomationOut(BaseModel):
    id: uuid.UUID
    name: str
    admin_id: uuid.UUID
    trigger_type: str
    flow: dict
    status: AutomationStatus
    recipients: int
    sent: int
    delivered: int
    replies: int
    created_at: datetime

    class Config:
        from_attributes = True
