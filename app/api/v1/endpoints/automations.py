import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import admin_scope_filter, get_current_user
from app.db.session import get_db
from app.models.user import User
from app.models.automation import Automation, AutomationRun
from app.models.activity_log import ActivityLog
from app.core.automation_templates import TEMPLATES, flow_of, get_template, starts_when, steps_of, trigger_of
from app.schemas.automation import (
    AutomationCreate, AutomationUpdate, AutomationOut, AutomationFromTemplate, AutomationTemplateOut,
)

router = APIRouter(prefix="/automations", tags=["automations"])


@router.get("", response_model=list[AutomationOut])
def list_automations(db: Session = Depends(get_db), scope: uuid.UUID | None = Depends(admin_scope_filter)):
    q = db.query(Automation)
    if scope:
        q = q.filter(Automation.admin_id == scope)
    return q.order_by(Automation.created_at.desc()).all()


@router.get("/templates", response_model=list[AutomationTemplateOut])
def list_templates(user: User = Depends(get_current_user)):
    """Ready-made automations anyone can start from."""
    return [AutomationTemplateOut(id=t["id"], name=t["name"], description=t["description"], starts_when=starts_when(t), steps=steps_of(t)) for t in TEMPLATES]


@router.post("/from-template", response_model=AutomationOut)
def create_from_template(payload: AutomationFromTemplate, db: Session = Depends(get_db), user: User = Depends(get_current_user), scope: uuid.UUID | None = Depends(admin_scope_filter)):
    """Creates an automation from a template. It starts as Scheduled: review it, then activate it."""
    template = get_template(payload.template_id)
    if not template:
        raise HTTPException(404, "Template not found")
    name = (payload.name or "").strip() or template["name"]
    ttype, tconfig = trigger_of(template)
    automation = Automation(name=name[:160], trigger_type=ttype, trigger_config=tconfig, flow=flow_of(template), admin_id=scope or user.id)
    db.add(automation)
    db.add(ActivityLog(actor_id=user.id, type="automation", message=f"{user.name} created automation \"{automation.name}\" from the \"{template['name']}\" template"))
    db.commit()
    db.refresh(automation)
    return automation


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


@router.delete("/{automation_id}", status_code=204)
def delete_automation(automation_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user), scope: uuid.UUID | None = Depends(admin_scope_filter)):
    """Deletes an automation and its run history. Messages it already sent stay in the chats."""
    automation = db.get(Automation, automation_id)
    if not automation or (scope and automation.admin_id != scope):
        raise HTTPException(404, "Automation not found")
    name = automation.name
    db.query(AutomationRun).filter(AutomationRun.automation_id == automation.id).delete(synchronize_session=False)
    db.delete(automation)
    db.add(ActivityLog(actor_id=user.id, type="automation", message=f"{user.name} deleted automation \"{name}\""))
    db.commit()
