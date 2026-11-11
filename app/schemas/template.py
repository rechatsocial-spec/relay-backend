import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class TemplateIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    text: str = Field(min_length=1, max_length=4000)


class TemplateOut(BaseModel):
    id: uuid.UUID
    name: str
    text: str
    admin_id: uuid.UUID
    created_at: datetime

    class Config:
        from_attributes = True
