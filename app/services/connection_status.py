"""
Live connection state of every linked WhatsApp number, asked from the provider
(not just read from our database), so the dashboard shows what is true right now.
Results are cached for a few seconds so many dashboards polling don't hammer the API.
"""
import logging
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.whatsapp_number import NumberStatus, WhatsAppNumber
from app.services.messaging import utcnow

log = logging.getLogger("relay.connection")

CACHE_SECONDS = 8
_cache: dict[str, tuple[float, str, str]] = {}  # account_id -> (fetched_at, connection, provider_status)

# provider account status -> (what the dashboard shows, our stored number status)
_MAP = {
    "running": ("connected", NumberStatus.ACTIVE),
    "degraded": ("attention", NumberStatus.WARNING),
    "partial": ("attention", NumberStatus.WARNING),
    "errored": ("disconnected", NumberStatus.ERROR),
    "disconnected": ("disconnected", NumberStatus.ERROR),
}


def _fetch(account_id: str) -> tuple[str, str]:
    """Returns (connection, provider_status). connection: connected|attention|disconnected|unknown."""
    hit = _cache.get(account_id)
    if hit and time.time() - hit[0] < CACHE_SECONDS:
        return hit[1], hit[2]
    try:
        r = httpx.get(
            f"{settings.UNIPILE_BASE_URL.rstrip('/')}/accounts/{account_id}",
            headers={"X-API-KEY": settings.UNIPILE_API_KEY, "accept": "application/json"}, timeout=8,
        )
        if r.status_code == 404:
            result = ("disconnected", "removed")
        elif r.status_code >= 300:
            return "unknown", f"http_{r.status_code}"
        else:
            body = r.json()
            status = body.get("status") or ""
            connection = _MAP.get(status, ("unknown", None))[0]
            if body.get("is_locked"):
                connection = "attention"
            result = (connection, status)
    except Exception as e:  # network trouble: don't cache, don't claim disconnected
        log.warning("Live status check failed for %s: %s", account_id, e)
        return "unknown", "unreachable"
    _cache[account_id] = (time.time(), *result)
    return result


def live_statuses(db: Session, numbers: list[WhatsAppNumber]) -> list[dict]:
    now = utcnow().isoformat()
    linked = [n for n in numbers if n.unipile_account_id]
    results: dict[str, tuple[str, str]] = {}
    if linked and not settings.WHATSAPP_MOCK and settings.UNIPILE_API_KEY:
        ids = sorted({n.unipile_account_id for n in linked})
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = dict(zip(ids, pool.map(_fetch, ids)))

    out, dirty = [], False
    for n in numbers:
        if not n.unipile_account_id:
            conn = "not_linked"
        elif settings.WHATSAPP_MOCK:
            conn = "demo"
        else:
            conn = results.get(n.unipile_account_id, ("unknown", ""))[0]
        # keep the stored status in step with what the provider reports
        new_status = {"connected": NumberStatus.ACTIVE, "attention": NumberStatus.WARNING, "disconnected": NumberStatus.ERROR}.get(conn)
        if new_status and n.status != new_status and n.status != NumberStatus.INACTIVE:
            n.status = new_status
            dirty = True
        out.append({"id": str(n.id), "phone": n.phone, "display_name": n.display_name, "admin_id": str(n.admin_id),
                    "connection": conn, "checked_at": now})
    if dirty:
        db.commit()
    return out
