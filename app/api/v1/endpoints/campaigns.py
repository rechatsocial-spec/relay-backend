import uuid

import threading

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import admin_scope_filter, get_current_user, get_owned_number
from app.db.session import get_db
from app.models.user import User
from app.models.campaign import Campaign, CampaignStatus, MessageContentType
from app.models.activity_log import ActivityLog
from app.schemas.campaign import (
    AudiencePreviewOut, AudiencePreviewRequest, CampaignCreate, CampaignOut, CampaignUpdate,
)
from app.services.campaign_runner import resolve_audience, run_campaign
from app.services.messaging import utcnow

router = APIRouter(prefix="/campaigns", tags=["campaigns"])


@router.get("", response_model=list[CampaignOut])
def list_campaigns(db: Session = Depends(get_db), scope: uuid.UUID | None = Depends(admin_scope_filter)):
    q = db.query(Campaign)
    if scope:
        q = q.filter(Campaign.admin_id == scope)
    return q.order_by(Campaign.created_at.desc()).all()


@router.post("/audience-preview", response_model=AudiencePreviewOut)
def audience_preview(payload: AudiencePreviewRequest, db: Session = Depends(get_db), scope: uuid.UUID | None = Depends(admin_scope_filter)):
    """How many contacts a campaign with this number + filter would reach."""
    number = get_owned_number(db, payload.whatsapp_number_id, scope)
    recipients, skipped = resolve_audience(db, number, payload.audience_filter.model_dump(exclude_none=True))
    return AudiencePreviewOut(recipients=len(recipients), opted_out_skipped=skipped)


@router.post("", response_model=CampaignOut)
def create_campaign(payload: CampaignCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user), scope: uuid.UUID | None = Depends(admin_scope_filter)):
    number = get_owned_number(db, payload.whatsapp_number_id, scope)
    data = payload.model_dump()
    data["audience_filter"] = {k: v for k, v in (data.get("audience_filter") or {}).items() if v}
    campaign = Campaign(**data, admin_id=number.admin_id, status=CampaignStatus.DRAFT)
    db.add(campaign)
    db.add(ActivityLog(actor_id=user.id, type="campaign", message=f"{user.name} created campaign \"{payload.name}\""))
    db.commit()
    db.refresh(campaign)
    return campaign


@router.post("/{campaign_id}/launch", response_model=CampaignOut)
def launch_campaign(
    campaign_id: uuid.UUID,
    db: Session = Depends(get_db), user: User = Depends(get_current_user),
    scope: uuid.UUID | None = Depends(admin_scope_filter),
):
    """Resolves the audience and starts sending in the background. Poll GET /campaigns for progress."""
    campaign = db.get(Campaign, campaign_id)
    if not campaign or (scope and campaign.admin_id != scope):
        raise HTTPException(404, "Campaign not found")
    if campaign.status not in (CampaignStatus.DRAFT, CampaignStatus.SCHEDULED, CampaignStatus.PAUSED):
        raise HTTPException(400, f"Cannot launch a campaign that is {campaign.status.value}")

    has_content = (campaign.message_text or "").strip() or campaign.content_sid or campaign.media_url
    if not has_content:
        raise HTTPException(400, "Campaign has no message, media or template to send")
    if campaign.message_type != MessageContentType.TEXT and not campaign.media_url and not campaign.content_sid:
        raise HTTPException(400, f"A media URL is required for a {campaign.message_type.value} campaign")

    number = get_owned_number(db, campaign.whatsapp_number_id, None)
    recipients, _ = resolve_audience(db, number, campaign.audience_filter)
    if not recipients:
        raise HTTPException(400, "No contacts match this campaign's audience")

    resuming = campaign.status == CampaignStatus.PAUSED
    if not resuming:
        campaign.recipients = len(recipients)
        campaign.started_at = utcnow()
    campaign.status = CampaignStatus.RUNNING
    db.add(ActivityLog(actor_id=user.id, type="campaign",
                       message=f"{user.name} {'resumed' if resuming else 'launched'} campaign \"{campaign.name}\" to {len(recipients)} contacts"))
    db.commit()
    db.refresh(campaign)
    # Own thread: the request returns immediately and sending survives slow pacing.
    threading.Thread(target=run_campaign, args=(campaign.id,), daemon=True, name=f"campaign-{campaign.id}").start()
    return campaign


@router.patch("/{campaign_id}", response_model=CampaignOut)
def update_campaign(campaign_id: uuid.UUID, payload: CampaignUpdate, db: Session = Depends(get_db), scope: uuid.UUID | None = Depends(admin_scope_filter)):
    """Use {"status": "Paused"} to pause a running campaign; launch again to resume."""
    campaign = db.get(Campaign, campaign_id)
    if not campaign or (scope and campaign.admin_id != scope):
        raise HTTPException(404, "Campaign not found")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(campaign, field, value)
    db.commit()
    db.refresh(campaign)
    return campaign
