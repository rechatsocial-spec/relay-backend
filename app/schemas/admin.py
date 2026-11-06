import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr

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


class AdminOut(BaseModel):
    id: uuid.UUID
    name: str
    email: str
    phone: str
    role: Role
    status: UserStatus
    created_at: datetime

    class Config:
        from_attributes = True


class AdminPerformanceOut(AdminOut):
    """Adds the aggregate columns the Super Admin dashboard table needs."""
    numbers_count: int
    messages_sent: int
    replies: int
    leads: int
    conversion_rate: float
