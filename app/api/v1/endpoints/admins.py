import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.deps import require_super_admin, get_current_user
from app.core.security import hash_password
from app.db.session import get_db
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
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(admin, field, value)
    db.commit()
    db.refresh(admin)
    return admin


@router.get("/me", response_model=AdminOut)
def my_profile(user: User = Depends(get_current_user)):
    return user
