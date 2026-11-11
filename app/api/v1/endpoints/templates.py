"""Saved messages. Each admin has their own; a Super Admin sees everyone's."""
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import admin_scope_filter, get_current_user
from app.db.session import get_db
from app.models.template import MessageTemplate
from app.models.user import User
from app.schemas.template import TemplateIn, TemplateOut

router = APIRouter(prefix="/templates", tags=["templates"])


def _mine(db: Session, template_id: uuid.UUID, scope) -> MessageTemplate:
    t = db.get(MessageTemplate, template_id)
    if not t or (scope and t.admin_id != scope):
        raise HTTPException(404, "Template not found")
    return t


@router.get("", response_model=list[TemplateOut])
def list_templates(db: Session = Depends(get_db), scope: uuid.UUID | None = Depends(admin_scope_filter)):
    q = db.query(MessageTemplate)
    if scope:
        q = q.filter(MessageTemplate.admin_id == scope)
    return q.order_by(MessageTemplate.name).all()


@router.post("", response_model=TemplateOut)
def create_template(payload: TemplateIn, db: Session = Depends(get_db), user: User = Depends(get_current_user),
                    scope: uuid.UUID | None = Depends(admin_scope_filter)):
    t = MessageTemplate(name=payload.name.strip(), text=payload.text.strip(), admin_id=scope or user.id)
    if not t.name or not t.text:
        raise HTTPException(400, "Name and text are required")
    db.add(t)
    db.commit()
    db.refresh(t)
    return t


@router.patch("/{template_id}", response_model=TemplateOut)
def update_template(template_id: uuid.UUID, payload: TemplateIn, db: Session = Depends(get_db),
                    scope: uuid.UUID | None = Depends(admin_scope_filter)):
    t = _mine(db, template_id, scope)
    t.name, t.text = payload.name.strip(), payload.text.strip()
    if not t.name or not t.text:
        raise HTTPException(400, "Name and text are required")
    db.commit()
    db.refresh(t)
    return t


@router.delete("/{template_id}", status_code=204)
def delete_template(template_id: uuid.UUID, db: Session = Depends(get_db), scope: uuid.UUID | None = Depends(admin_scope_filter)):
    db.delete(_mine(db, template_id, scope))
    db.commit()
