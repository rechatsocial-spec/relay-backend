import uuid
from datetime import datetime

from pydantic import BaseModel

from app.models.campaign import CampaignStatus, MessageContentType


class CampaignCreate(BaseModel):
    name: str
    whatsapp_number_id: uuid.UUID
    audience_filter: dict = {}
    message_type: MessageContentType = MessageContentType.TEXT
    message_text: str | None = None
    media_url: str | None = None
    content_sid: str | None = None
    content_variables: dict | None = None
    scheduled_at: datetime | None = None


class AudienceFilter(BaseModel):
    """Who a campaign goes to. Empty = all of the number owner's contacts."""
    status: str | None = None
    tag: str | None = None


class AudiencePreviewRequest(BaseModel):
    whatsapp_number_id: uuid.UUID
    audience_filter: AudienceFilter = AudienceFilter()


class AudiencePreviewOut(BaseModel):
    recipients: int
    opted_out_skipped: int


class CampaignUpdate(BaseModel):
    status: CampaignStatus | None = None
    scheduled_at: datetime | None = None


class CampaignOut(BaseModel):
    id: uuid.UUID
    name: str
    whatsapp_number_id: uuid.UUID
    admin_id: uuid.UUID
    message_type: MessageContentType
    message_text: str | None = None
    media_url: str | None = None
    content_sid: str | None = None
    content_variables: dict | None = None
    audience_filter: dict | None = None
    status: CampaignStatus
    recipients: int
    sent: int
    delivered: int
    read: int
    replies: int
    failed: int
    scheduled_at: datetime | None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime

    class Config:
        from_attributes = True
