import uuid

from sqlalchemy import func
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, admin_scope_filter, require_super_admin
from app.db.session import get_db
from app.models.user import User
from app.models.conversation import Conversation
from app.models.whatsapp_number import WhatsAppNumber, NumberStatus
from app.models.activity_log import ActivityLog
from app.schemas.whatsapp_number import WhatsAppNumberCreate, WhatsAppNumberUpdate, WhatsAppNumberOut
from app.services.connection_status import live_statuses

router = APIRouter(prefix="/numbers", tags=["whatsapp-numbers"])


@router.get("", response_model=list[WhatsAppNumberOut])
def list_numbers(
    status_filter: NumberStatus | None = Query(None, alias="status"),
    db: Session = Depends(get_db),
    scope: uuid.UUID | None = Depends(admin_scope_filter),
):
    q = db.query(WhatsAppNumber)
    if scope:
        q = q.filter(WhatsAppNumber.admin_id == scope)
    if status_filter:
        q = q.filter(WhatsAppNumber.status == status_filter)
    rows = q.order_by(WhatsAppNumber.created_at.desc()).all()
    if rows:
        stats = {
            nid: (chats, unread)
            for nid, chats, unread in db.query(
                Conversation.whatsapp_number_id, func.count(Conversation.id), func.coalesce(func.sum(Conversation.unread_count), 0)
            ).filter(Conversation.whatsapp_number_id.in_([n.id for n in rows])).group_by(Conversation.whatsapp_number_id)
        }
        for n in rows:
            n.chats, n.unread = stats.get(n.id, (0, 0))
    return rows


@router.get("/live-status")
def numbers_live_status(db: Session = Depends(get_db), scope: uuid.UUID | None = Depends(admin_scope_filter)):
    """Real-time connection state of each number, checked with the WhatsApp provider."""
    q = db.query(WhatsAppNumber)
    if scope:
        q = q.filter(WhatsAppNumber.admin_id == scope)
    return live_statuses(db, q.order_by(WhatsAppNumber.created_at.desc()).all())


@router.post("", response_model=WhatsAppNumberOut, dependencies=[Depends(require_super_admin)])
def add_number(payload: WhatsAppNumberCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    if db.query(WhatsAppNumber).filter(WhatsAppNumber.phone == payload.phone).first():
        raise HTTPException(400, "This phone number is already connected")
    number = WhatsAppNumber(**payload.model_dump())
    db.add(number)
    db.add(ActivityLog(actor_id=user.id, type="whatsapp_number", message=f"WhatsApp number {payload.phone} connected"))
    db.commit()
    db.refresh(number)
    return number


@router.patch("/{number_id}", response_model=WhatsAppNumberOut)
def update_number(
    number_id: uuid.UUID, payload: WhatsAppNumberUpdate,
    db: Session = Depends(get_db), scope: uuid.UUID | None = Depends(admin_scope_filter),
):
    number = db.get(WhatsAppNumber, number_id)
    if not number or (scope and number.admin_id != scope):
        raise HTTPException(404, "Number not found")
    changes = payload.model_dump(exclude_unset=True)
    # Reassigning a number to a different admin is a Super Admin action only;
    # otherwise an Admin could hand their number (and its traffic) to anyone.
    if "admin_id" in changes and scope is not None:
        raise HTTPException(403, "Only a Super Admin can reassign a number")
    for field, value in changes.items():
        setattr(number, field, value)
    db.commit()
    db.refresh(number)
    return number


@router.post("/{number_id}/deactivate", response_model=WhatsAppNumberOut, dependencies=[Depends(require_super_admin)])
def deactivate_number(number_id: uuid.UUID, db: Session = Depends(get_db)):
    number = db.get(WhatsAppNumber, number_id)
    if not number:
        raise HTTPException(404, "Number not found")
    number.status = NumberStatus.INACTIVE
    db.commit()
    db.refresh(number)
    return number
