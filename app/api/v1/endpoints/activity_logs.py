from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import require_super_admin
from app.db.session import get_db
from app.models.activity_log import ActivityLog
from app.schemas.api_key import ActivityLogOut

router = APIRouter(prefix="/activity-logs", tags=["activity-logs"], dependencies=[Depends(require_super_admin)])


@router.get("", response_model=list[ActivityLogOut])
def list_activity(
    type_filter: str | None = Query(None, alias="type"),
    limit: int = Query(50, le=200),
    db: Session = Depends(get_db),
):
    q = db.query(ActivityLog)
    if type_filter:
        q = q.filter(ActivityLog.type == type_filter)
    return q.order_by(ActivityLog.created_at.desc()).limit(limit).all()
