from fastapi import APIRouter, Depends

from app.api.deps import get_current_user, require_super_admin
from app.core.config import settings
from app.models.user import Role, User
from app.services.diagnostics import run_connection_check
from app.services.sync import STATE
from app.services.webhook_stats import STATS as WEBHOOK_STATS

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
        "webhook": dict(WEBHOOK_STATS) if user.role == Role.SUPER_ADMIN else None,
    }


@router.post("/connection-check", dependencies=[Depends(require_super_admin)])
def connection_check():
    """Asks the WhatsApp service (with this server's key) why texts might not be arriving, and answers in plain words."""
    return run_connection_check()
