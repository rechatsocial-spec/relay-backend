import uuid

from fastapi import APIRouter, Depends, HTTPException
import logging

from sqlalchemy import func, or_, select
from sqlalchemy.exc import SQLAlchemyError
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
from app.services.stats import message_counts
from app.schemas.admin import AdminCreate, AdminUpdate, AdminOut, AdminPerformanceOut

log = logging.getLogger("relay.admins")
router = APIRouter(prefix="/admins", tags=["admins"])

LEAD_STATUSES = [ContactStatus.QUALIFIED, ContactStatus.DEMO, ContactStatus.CONVERTED]


@router.get("", response_model=list[AdminPerformanceOut], dependencies=[Depends(require_super_admin)])
def list_admins(db: Session = Depends(get_db)):
    admins = db.query(User).filter(User.role == Role.ADMIN).all()
    out = []
    for a in admins:
        numbers_count = db.query(func.count(WhatsAppNumber.id)).filter(WhatsAppNumber.admin_id == a.id).scalar() or 0
        counts = message_counts(db, admin_id=a.id)
        messages_sent, replies = counts["sent"], counts["received"]
        contacts = db.query(func.count(Contact.id)).filter(Contact.admin_id == a.id).scalar() or 0
        leads = db.query(func.count(Contact.id)).filter(Contact.admin_id == a.id, Contact.status.in_(LEAD_STATUSES)).scalar() or 0
        conversion = round((leads / contacts) * 100, 1) if contacts else 0.0
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


def _fail(db: Session, e: Exception):
    """Roll back and return the real DB error as JSON (with CORS headers), so
    the UI shows it instead of a generic 'could not reach backend'."""
    db.rollback()
    log.exception("admin delete failed")
    detail = str(getattr(e, "orig", e)).strip().splitlines()[0][:300]
    raise HTTPException(500, f"Delete failed ({type(e).__name__}): {detail}")


def _purge_admin(db: Session, admin: User) -> None:
    """Delete an Admin and everything attached to them. Caller commits.

    Rows are matched by owner (admin_id) OR by the admin's WhatsApp numbers,
    because a row can point at one of this admin's numbers while being owned
    by someone else; leaving it would break the numbers' foreign keys.
    """
    aid = admin.id
    nums = select(WhatsAppNumber.id).where(WhatsAppNumber.admin_id == aid)
    contacts = select(Contact.id).where(or_(Contact.admin_id == aid, Contact.whatsapp_number_id.in_(nums)))
    convos = select(Conversation.id).where(or_(
        Conversation.admin_id == aid, Conversation.whatsapp_number_id.in_(nums), Conversation.contact_id.in_(contacts)))
    campaigns = select(Campaign.id).where(or_(Campaign.admin_id == aid, Campaign.whatsapp_number_id.in_(nums)))

    db.query(Message).filter(or_(Message.conversation_id.in_(convos), Message.campaign_id.in_(campaigns)))         .delete(synchronize_session=False)
    db.query(Conversation).filter(Conversation.id.in_(convos)).delete(synchronize_session=False)
    db.query(Campaign).filter(Campaign.id.in_(campaigns)).delete(synchronize_session=False)
    db.query(Chatbot).filter(or_(Chatbot.admin_id == aid, Chatbot.whatsapp_number_id.in_(nums)))         .delete(synchronize_session=False)
    db.query(Automation).filter(Automation.admin_id == aid).delete(synchronize_session=False)
    db.query(Contact).filter(Contact.id.in_(contacts)).delete(synchronize_session=False)
    db.query(WhatsAppNumber).filter(WhatsAppNumber.admin_id == aid).delete(synchronize_session=False)
    # Keep the audit trail; just detach the deleted user.
    db.query(ActivityLog).filter(ActivityLog.actor_id == aid).update({ActivityLog.actor_id: None}, synchronize_session=False)
    db.query(ApiKey).filter(ApiKey.updated_by == aid).update({ApiKey.updated_by: None}, synchronize_session=False)
    db.expire_all()
    db.delete(admin)


@router.delete("", dependencies=[Depends(require_super_admin)])
def delete_all_admins(db: Session = Depends(get_db), me: User = Depends(require_super_admin)):
    """Permanently removes EVERY Admin (role=admin) and all their data.
    Super Admin accounts are never touched."""
    admins = db.query(User).filter(User.role == Role.ADMIN).all()
    try:
        for a in admins:
            _purge_admin(db, a)
        db.add(ActivityLog(actor_id=me.id, type="admin", message=f"{me.name} deleted all admins ({len(admins)})"))
        db.commit()
    except SQLAlchemyError as e:
        _fail(db, e)
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
    try:
        _purge_admin(db, admin)
        db.commit()
    except SQLAlchemyError as e:
        _fail(db, e)


@router.get("/me", response_model=AdminOut)
def my_profile(user: User = Depends(get_current_user)):
    return user
