import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.deps import require_super_admin, get_current_user
from app.core.security import hash_password
from app.db.session import get_db
from app.core.pages import TOGGLEABLE_PAGES
from app.models.activity_log import ActivityLog
from app.models.api_key import ApiKey
from app.models.automation import Automation
from app.models.campaign import Campaign
from app.models.chatbot import Chatbot
from app.models.conversation import Conversation, Message
from app.models.user import User, Role
from app.models.whatsapp_number import WhatsAppNumber
from app.models.contact import Contact, ContactStatus
from app.schemas.admin import AdminCreate, AdminUpdate, AdminOut, AdminPerformanceOut

router = APIRouter(prefix="/admins", tags=["admins"])

LEAD_STATUSES = [ContactStatus.QUALIFIED, ContactStatus.DEMO, ContactStatus.CONVERTED]


@router.get("", response_model=list[AdminPerformanceOut], dependencies=[Depends(require_super_admin)])
def list_admins(db: Session = Depends(get_db)):
    admins = db.query(User).filter(User.role == Role.ADMIN).all()
    out = []
    for a in admins:
        numbers_count = db.query(func.count(WhatsAppNumber.id)).filter(WhatsAppNumber.admin_id == a.id).scalar() or 0
        messages_sent = db.query(func.coalesce(func.sum(WhatsAppNumber.messages_today), 0)).filter(WhatsAppNumber.admin_id == a.id).scalar() or 0
        leads = db.query(func.count(Contact.id)).filter(Contact.admin_id == a.id, Contact.status.in_(LEAD_STATUSES)).scalar() or 0
        replies = db.query(func.count(Contact.id)).filter(Contact.admin_id == a.id, Contact.status != ContactStatus.NEW).scalar() or 0
        conversion = round((leads / messages_sent) * 100, 1) if messages_sent else 0.0
        out.append(AdminPerformanceOut(
            **AdminOut.model_validate(a).model_dump(),
            numbers_count=numbers_count, messages_sent=messages_sent,
            replies=replies, leads=leads, conversion_rate=conversion,
        ))
    return out


@router.post("", response_model=AdminOut, dependencies=[Depends(require_super_admin)])
def create_admin(payload: AdminCreate, db: Session = Depends(get_db)):
    if db.query(User).filter(User.email == payload.email).first():
        raise HTTPException(400, "An account with this email already exists")
    admin = User(
        name=payload.name, email=payload.email, phone=payload.phone,
        hashed_password=hash_password(payload.password), role=payload.role,
    )
    db.add(admin)
    db.commit()
    db.refresh(admin)
    return admin


@router.patch("/{admin_id}", response_model=AdminOut, dependencies=[Depends(require_super_admin)])
def update_admin(admin_id: uuid.UUID, payload: AdminUpdate, db: Session = Depends(get_db)):
    admin = db.get(User, admin_id)
    if not admin:
        raise HTTPException(404, "Admin not found")
    data = payload.model_dump(exclude_unset=True)
    if "hidden_pages" in data:
        pages = data.pop("hidden_pages") or []
        bad = [p for p in pages if p not in TOGGLEABLE_PAGES]
        if bad:
            raise HTTPException(400, f"Unknown page(s): {', '.join(bad)}")
        admin.hidden_pages = ",".join(dict.fromkeys(pages))
    for field, value in data.items():
        setattr(admin, field, value)
    db.commit()
    db.refresh(admin)
    return admin


def _purge_admin(db: Session, admin: User) -> None:
    """Delete an Admin and everything they own. Caller commits."""
    admin_id = admin.id
    convo_ids = [c for (c,) in db.query(Conversation.id).filter(Conversation.admin_id == admin_id)]
    if convo_ids:
        db.query(Message).filter(Message.conversation_id.in_(convo_ids)).delete(synchronize_session=False)
    for model in (Conversation, Campaign, Automation, Chatbot, Contact):
        db.query(model).filter(model.admin_id == admin_id).delete(synchronize_session=False)
    db.query(WhatsAppNumber).filter(WhatsAppNumber.admin_id == admin_id).delete(synchronize_session=False)
    # Keep the audit trail; just detach the deleted user.
    db.query(ActivityLog).filter(ActivityLog.actor_id == admin_id).update({ActivityLog.actor_id: None}, synchronize_session=False)
    db.query(ApiKey).filter(ApiKey.updated_by == admin_id).update({ApiKey.updated_by: None}, synchronize_session=False)
    db.delete(admin)


@router.delete("", dependencies=[Depends(require_super_admin)])
def delete_all_admins(db: Session = Depends(get_db), me: User = Depends(require_super_admin)):
    """Permanently removes EVERY Admin (role=admin) and all their data.
    Super Admin accounts are never touched."""
    admins = db.query(User).filter(User.role == Role.ADMIN).all()
    for a in admins:
        _purge_admin(db, a)
    db.add(ActivityLog(actor_id=me.id, type="admin", message=f"{me.name} deleted all admins ({len(admins)})"))
    db.commit()
    return {"deleted": len(admins)}


@router.delete("/{admin_id}", status_code=204)
def delete_admin(admin_id: uuid.UUID, db: Session = Depends(get_db), me: User = Depends(require_super_admin)):
    """Permanently removes an Admin AND everything they own. Super Admins
    can't be deleted here, and you can't delete yourself."""
    admin = db.get(User, admin_id)
    if not admin:
        raise HTTPException(404, "Admin not found")
    if admin.role == Role.SUPER_ADMIN or admin.id == me.id:
        raise HTTPException(400, "Super Admin accounts can't be deleted here")
    db.add(ActivityLog(actor_id=me.id, type="admin", message=f"{me.name} deleted admin {admin.name} ({admin.email})"))
    _purge_admin(db, admin)
    db.commit()


@router.get("/me", response_model=AdminOut)
def my_profile(user: User = Depends(get_current_user)):
    return user
