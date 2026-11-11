"""Signed links for stored and received media (an <img>/<audio> tag cannot send a login header)."""
import hashlib
import hmac

from app.core.config import settings


def sign(key: str) -> str:
    return hmac.new(settings.SECRET_KEY.encode(), f"media:{key}".encode(), hashlib.sha256).hexdigest()[:32]


def verify(key: str, signature: str) -> bool:
    return hmac.compare_digest(sign(key), signature or "")


def stored_url(file_id) -> str:
    return f"{settings.API_V1_PREFIX}/media/{file_id}?t={sign(str(file_id))}"


def proxy_url(message_id) -> str:
    """A received file: the app fetches it from the WhatsApp service when the browser asks."""
    return f"{settings.API_V1_PREFIX}/media/m/{message_id}?t={sign(str(message_id))}"
