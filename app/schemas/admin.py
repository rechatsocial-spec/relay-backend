import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr, field_validator

from app.core.pages import DEFAULT_HIDDEN_PAGES

from app.models.user import Role, UserStatus


class AdminCreate(BaseModel):
    name: str
    email: EmailStr
    phone: str
    password: str
    role: Role = Role.ADMIN


class AdminUpdate(BaseModel):
    name: str | None = None
    phone: str | None = None
    status: UserStatus | None = None
    hidden_pages: list[str] | None = None


class AdminOut(BaseModel):
    id: uuid.UUID
    name: str
    email: str
    phone: str
    role: Role
    status: UserStatus
    created_at: datetime
    hidden_pages: list[str] = []

    class Config:
        from_attributes = True

    @field_validator("hidden_pages", mode="before")
    @classmethod
    def _split_hidden(cls, v, info):
        if isinstance(v, list):
            return v
        if v is None:
            return list(DEFAULT_HIDDEN_PAGES)
        return [p for p in v.split(",") if p]


class AdminPerformanceOut(AdminOut):
    """Adds the aggregate columns the Super Admin dashboard table needs."""
    numbers_count: int
    messages_sent: int
    replies: int
    leads: int
    conversion_rate: float
