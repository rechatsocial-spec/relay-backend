import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.deps import admin_scope_filter
from app.db.session import get_db
from app.models.contact import Contact, ContactStatus
from app.schemas.analytics import AnalyticsSummary, FunnelStage, TimePoint
from app.services.stats import daily_series, message_counts

router = APIRouter(prefix="/analytics", tags=["analytics"])

LEAD_STATUSES = [ContactStatus.QUALIFIED, ContactStatus.DEMO, ContactStatus.CONVERTED]


@router.get("/summary", response_model=AnalyticsSummary)
def summary(
    admin_id: uuid.UUID | None = None,
    number_id: uuid.UUID | None = None,
    db: Session = Depends(get_db),
    scope: uuid.UUID | None = Depends(admin_scope_filter),
):
    """Totals counted from the actual messages (see app/services/stats.py)."""
    effective_admin = scope or admin_id
    c = message_counts(db, effective_admin, number_id)

    cq = db.query(func.count(Contact.id))
    if effective_admin:
        cq = cq.filter(Contact.admin_id == effective_admin)
    if number_id:
        cq = cq.filter(Contact.whatsapp_number_id == number_id)
    total_contacts = cq.scalar() or 0
    leads = cq.filter(Contact.status.in_(LEAD_STATUSES)).scalar() or 0
    conversion = round(leads / total_contacts * 100, 1) if total_contacts else 0.0

    return AnalyticsSummary(
        messages_sent=c["sent"], delivered=c["delivered"], read=c["read"], failed=c["failed"],
        replies=c["received"], leads=leads, conversion_rate=conversion,
    )


@router.get("/timeseries", response_model=list[TimePoint])
def timeseries(
    days: int = Query(14, ge=1, le=90),
    admin_id: uuid.UUID | None = None,
    number_id: uuid.UUID | None = None,
    db: Session = Depends(get_db),
    scope: uuid.UUID | None = Depends(admin_scope_filter),
):
    """Messages per day for the last `days` days (UTC), counted from the actual messages."""
    return daily_series(db, days, scope or admin_id, number_id)


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
