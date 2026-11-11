from fastapi import APIRouter, Depends

from app.api.deps import get_current_user
from app.core.config import settings
from app.models.user import Role, User
from app.services.sync import STATE

router = APIRouter(prefix="/system", tags=["system"], dependencies=[Depends(get_current_user)])


@router.get("/whatsapp")
def whatsapp_status(user: User = Depends(get_current_user)):
    """What the Settings page shows: is WhatsApp live, and the webhook URL to register with the provider."""
    base = settings.PUBLIC_BASE_URL.rstrip("/")
    return {
        "provider": "mock" if settings.WHATSAPP_MOCK else "whatsapp",
        "live": not settings.WHATSAPP_MOCK,
        "credentials_set": bool(settings.UNIPILE_API_KEY and settings.UNIPILE_WEBHOOK_SECRET),
        "public_base_url": base or None,
        "inbound_webhook_url": f"{base}{settings.API_V1_PREFIX}/whatsapp/webhook" if base else None,
        "send_interval_seconds": settings.CAMPAIGN_SEND_INTERVAL_SECONDS,
        "reply_delay": {"min": settings.AUTOMATION_REPLY_DELAY_MIN, "max": settings.AUTOMATION_REPLY_DELAY_MAX},
        "sync": {**STATE, "error": STATE["error"] if user.role == Role.SUPER_ADMIN else None},
    }
