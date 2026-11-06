from fastapi import APIRouter, Depends

from app.api.deps import get_current_user
from app.core.config import settings

router = APIRouter(prefix="/system", tags=["system"], dependencies=[Depends(get_current_user)])


@router.get("/whatsapp")
def whatsapp_status():
    """What the Settings page shows: which provider is live and the webhook URLs to give Twilio."""
    provider = "mock" if settings.WHATSAPP_MOCK else settings.WHATSAPP_PROVIDER.lower()
    base = settings.PUBLIC_BASE_URL.rstrip("/")
    prefix = settings.API_V1_PREFIX
    return {
        "provider": provider,
        "live": provider != "mock",
        "credentials_set": bool(settings.WASENDER_API_KEY) if provider == "wasender"
        else bool(settings.TWILIO_ACCOUNT_SID and settings.TWILIO_AUTH_TOKEN),
        "account_sid_hint": (settings.TWILIO_ACCOUNT_SID[:6] + "…" + settings.TWILIO_ACCOUNT_SID[-4:]) if settings.TWILIO_ACCOUNT_SID else None,
        "public_base_url": base or None,
        "inbound_webhook_url": f"{base}{prefix}/webhooks/twilio/inbound" if base else None,
        "status_webhook_url": f"{base}{prefix}/webhooks/twilio/status" if base else None,
        "send_interval_seconds": settings.CAMPAIGN_SEND_INTERVAL_SECONDS,
    }
