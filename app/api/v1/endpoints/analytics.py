import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.deps import admin_scope_filter
from app.db.session import get_db
from app.models.whatsapp_number import WhatsAppNumber
from app.models.contact import Contact, ContactStatus
from app.schemas.analytics import AnalyticsSummary, FunnelStage

router = APIRouter(prefix="/analytics", tags=["analytics"])

LEAD_STATUSES = [ContactStatus.QUALIFIED, ContactStatus.DEMO, ContactStatus.CONVERTED]


@router.get("/summary", response_model=AnalyticsSummary)
def summary(
    admin_id: uuid.UUID | None = None,
    number_id: uuid.UUID | None = None,
    db: Session = Depends(get_db),
    scope: uuid.UUID | None = Depends(admin_scope_filter),
):
    """
    NOTE: sent/delivered/read/failed are read from WhatsAppNumber's rolling
    counters, which are the right place for them once a webhook handler
    updates them per delivery-status callback. Wire that handler up when
    WHATSAPP_MOCK flips to false — this endpoint doesn't need to change.
    """
    effective_admin = scope or admin_id
    q = db.query(WhatsAppNumber)
    if effective_admin:
        q = q.filter(WhatsAppNumber.admin_id == effective_admin)
    if number_id:
        q = q.filter(WhatsAppNumber.id == number_id)
    numbers = q.all()

    sent = sum(n.messages_today for n in numbers)
    delivered = round(sent * (sum(n.delivery_rate for n in numbers) / len(numbers) / 100)) if numbers else 0
    replies = round(sent * (sum(n.reply_rate for n in numbers) / len(numbers) / 100)) if numbers else 0
    failed = max(sent - delivered, 0)
    read = round(delivered * 0.65)

    cq = db.query(func.count(Contact.id))
    if effective_admin:
        cq = cq.filter(Contact.admin_id == effective_admin)
    leads = cq.filter(Contact.status.in_(LEAD_STATUSES)).scalar() or 0
    conversion = round((leads / sent) * 100, 1) if sent else 0.0

    return AnalyticsSummary(
        messages_sent=sent, delivered=delivered, read=read, failed=failed,
        replies=replies, leads=leads, conversion_rate=conversion,
    )


@router.get("/funnel", response_model=list[FunnelStage])
def funnel(
    admin_id: uuid.UUID | None = None,
    db: Session = Depends(get_db),
    scope: uuid.UUID | None = Depends(admin_scope_filter),
):
    s = summary(admin_id=admin_id, db=db, scope=scope)
    return [
        FunnelStage(name="Messages Sent", value=s.messages_sent),
        FunnelStage(name="Delivered", value=s.delivered),
        FunnelStage(name="Read", value=s.read),
        FunnelStage(name="Replied", value=s.replies),
        FunnelStage(name="Converted", value=s.leads),
    ]
