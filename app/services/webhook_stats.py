"""What the incoming-notification endpoint has seen since this server started (shown in the connection check)."""
from app.services.messaging import utcnow

STATS = {
    "received": 0, "last_at": None, "last_type": None, "last_account": None,
    "rejected_signature": 0, "rejected_stale": 0, "last_rejected_at": None,
    "unknown_account": 0, "last_unknown_account": None,
    "selftest_at": None,
}


def note(**fields) -> None:
    STATS.update(fields)


def bump(key: str) -> None:
    STATS[key] = STATS.get(key, 0) + 1


def now_iso() -> str:
    return utcnow().isoformat()
