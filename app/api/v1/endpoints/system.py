from fastapi import APIRouter, Depends

from app.api.deps import get_current_user
from app.core.config import settings

router = APIRouter(prefix="/system", tags=["system"], dependencies=[Depends(get_current_user)])


@router.get("/whatsapp")
def whatsapp_status():
    """What the Settings page shows: is WhatsApp live (via Unipile) and the webhook URL to give Unipile."""
    base = settings.PUBLIC_BASE_URL.rstrip("/")
    return {
        "provider": "mock" if settings.WHATSAPP_MOCK else "unipile",
        "live": not settings.WHATSAPP_MOCK,
        "credentials_set": bool(settings.UNIPILE_API_KEY and settings.UNIPILE_WEBHOOK_SECRET),
        "public_base_url": base or None,
        "inbound_webhook_url": f"{base}{settings.API_V1_PREFIX}/unipile/webhook" if base else None,
        "send_interval_seconds": settings.CAMPAIGN_SEND_INTERVAL_SECONDS,
    }
