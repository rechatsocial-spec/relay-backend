import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import admin_scope_filter, get_current_user, get_owned_number
from app.db.session import get_db
from app.models.user import User
from app.models.chatbot import Chatbot
from app.schemas.chatbot import ChatbotCreate, ChatbotUpdate, ChatbotOut

router = APIRouter(prefix="/chatbots", tags=["chatbot"])


@router.get("", response_model=list[ChatbotOut])
def list_chatbots(db: Session = Depends(get_db), scope: uuid.UUID | None = Depends(admin_scope_filter)):
    q = db.query(Chatbot)
    if scope:
        q = q.filter(Chatbot.admin_id == scope)
    return q.order_by(Chatbot.created_at.desc()).all()


@router.post("", response_model=ChatbotOut)
def create_chatbot(payload: ChatbotCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user), scope: uuid.UUID | None = Depends(admin_scope_filter)):
    number = get_owned_number(db, payload.whatsapp_number_id, scope)
    bot = Chatbot(**payload.model_dump(), admin_id=number.admin_id)
    db.add(bot)
    db.commit()
    db.refresh(bot)
    return bot


@router.patch("/{bot_id}", response_model=ChatbotOut)
def update_chatbot(bot_id: uuid.UUID, payload: ChatbotUpdate, db: Session = Depends(get_db), scope: uuid.UUID | None = Depends(admin_scope_filter)):
    bot = db.get(Chatbot, bot_id)
    if not bot or (scope and bot.admin_id != scope):
        raise HTTPException(404, "Chatbot not found")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(bot, field, value)
    db.commit()
    db.refresh(bot)
    return bot
