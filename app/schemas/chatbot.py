import uuid
from datetime import datetime

from pydantic import BaseModel

from app.models.chatbot import ChatbotStatus


class ChatbotCreate(BaseModel):
    name: str
    whatsapp_number_id: uuid.UUID
    flow: dict = {}
    keywords: list[str] = []


class ChatbotUpdate(BaseModel):
    name: str | None = None
    flow: dict | None = None
    keywords: list[str] | None = None
    status: ChatbotStatus | None = None


class ChatbotOut(BaseModel):
    id: uuid.UUID
    name: str
    whatsapp_number_id: uuid.UUID
    admin_id: uuid.UUID
    flow: dict
    keywords: list[str]
    status: ChatbotStatus
    conversations: int
    automated_responses: int
    human_handoffs: int
    created_at: datetime

    class Config:
        from_attributes = True
