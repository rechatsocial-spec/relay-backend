import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.models.contact import ContactStatus


class ContactCreate(BaseModel):
    name: str
    phone: str
    email: str | None = None
    city: str | None = None
    whatsapp_number_id: uuid.UUID
    tags: list[str] = []


class ContactUpdate(BaseModel):
    name: str | None = None
    email: str | None = None
    status: ContactStatus | None = None
    tags: list[str] | None = None
    notes: str | None = None
    opted_out: bool | None = None


class ContactOut(BaseModel):
    id: uuid.UUID
    name: str
    phone: str
    email: str | None
    city: str | None
    whatsapp_number_id: uuid.UUID
    admin_id: uuid.UUID
    status: ContactStatus
    tags: list[str]
    notes: str | None
    opted_out: bool = False
    source: str | None = None
    last_contact_at: datetime | None
    created_at: datetime

    class Config:
        from_attributes = True


class AddNoteRequest(BaseModel):
    note: str


class AddTagRequest(BaseModel):
    tag: str


class ImportRow(BaseModel):
    phone: str
    name: str | None = None
    tags: list[str] = []


class ContactImportRequest(BaseModel):
    whatsapp_number_id: uuid.UUID
    rows: list[ImportRow] = Field(min_length=1, max_length=20000)
    # Added to every imported contact, so a campaign can target exactly this list.
    tag: str | None = None
    default_country_code: str = Field("91", pattern=r"^\+?\d{1,3}$")
    # Must be true: the uploader confirms these people agreed to WhatsApp messages.
    consent: bool


class ImportRowError(BaseModel):
    row: int
    value: str
    reason: str


class ContactImportResult(BaseModel):
    created: int
    updated: int
    duplicates_in_file: int
    opted_out: int
    invalid: list[ImportRowError]
    tag: str | None
