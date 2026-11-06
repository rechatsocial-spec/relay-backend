import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import admin_scope_filter, get_current_user
from app.db.session import get_db
from app.models.user import User
from app.models.automation import Automation
from app.models.activity_log import ActivityLog
from app.schemas.automation import AutomationCreate, AutomationUpdate, AutomationOut

router = APIRouter(prefix="/automations", tags=["automations"])


@router.get("", response_model=list[AutomationOut])
def list_automations(db: Session = Depends(get_db), scope: uuid.UUID | None = Depends(admin_scope_filter)):
    q = db.query(Automation)
    if scope:
        q = q.filter(Automation.admin_id == scope)
    return q.order_by(Automation.created_at.desc()).all()


@router.post("", response_model=AutomationOut)
def create_automation(payload: AutomationCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user), scope: uuid.UUID | None = Depends(admin_scope_filter)):
    automation = Automation(**payload.model_dump(), admin_id=scope or user.id)
    db.add(automation)
    db.commit()
    db.refresh(automation)
    return automation


@router.patch("/{automation_id}", response_model=AutomationOut)
def update_automation(automation_id: uuid.UUID, payload: AutomationUpdate, db: Session = Depends(get_db), user: User = Depends(get_current_user), scope: uuid.UUID | None = Depends(admin_scope_filter)):
    automation = db.get(Automation, automation_id)
    if not automation or (scope and automation.admin_id != scope):
        raise HTTPException(404, "Automation not found")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(automation, field, value)
    if payload.status == "Running":
        db.add(ActivityLog(actor_id=user.id, type="automation", message=f"{user.name} started automation \"{automation.name}\""))
    db.commit()
    db.refresh(automation)
    return automation
