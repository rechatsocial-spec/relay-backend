import logging
import threading

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.db.base import Base
from app.db.session import engine
from app.db.auto_migrate import upgrade_schema

from app.bootstrap import ensure_bootstrap_admin, warn_default_passwords
from app.services.automation_runner import start_poller
from app.services.campaign_runner import resume_interrupted_campaigns
from app.services.sync import start_sync_loop
from app.services.demo_cleanup import cleanup_demo_data_on_start
from app.api.v1.api import api_router

app = FastAPI(title=settings.PROJECT_NAME)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    # False is correct here, not a shortcut: auth uses a Bearer token in the
    # Authorization header, not cookies, so the browser's credentialed-CORS
    # rules (which forbid combining allow_origins=["*"] with credentials)
    # never come into play. If cookie-based auth is added later, this must
    # become True AND CORS_ORIGINS must be an explicit list, not "*".
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix=settings.API_V1_PREFIX)


@app.on_event("startup")
def on_startup():
    # Dev convenience only — creates tables that don't exist yet.
    # Use Alembic migrations (see README) instead of this once you have
    # real data you can't afford to lose on a schema change.
    Base.metadata.create_all(bind=engine)
    upgrade_schema(engine)  # adds new columns to existing tables (dev convenience)
    ensure_bootstrap_admin()
    cleanup_demo_data_on_start()  # removes the demo seed; real numbers and chats are never touched
    import os

    seen = sorted(k for k in os.environ if k.upper().startswith("UNIPILE"))
    log = logging.getLogger("relay")
    log.warning("WhatsApp settings found on this server: %s", ", ".join(seen) or "none")
    if not settings.WHATSAPP_MOCK and not settings.UNIPILE_API_KEY:
        log.error("WhatsApp is live but no API key was found (set UNIPILE_API_KEY) — nothing can be sent or imported")
    if not settings.UNIPILE_WEBHOOK_SECRET:
        log.error("No webhook secret was found (set UNIPILE_WEBHOOK_SECRET) — every incoming-message notification will be refused")
    threading.Thread(target=warn_default_passwords, daemon=True).start()  # bcrypt checks: keep startup fast
    resume_interrupted_campaigns()
    start_sync_loop()  # imports connected WhatsApp accounts and their recent chats
    start_poller()  # runs due automation steps (welcome messages, waits, follow-ups)  # campaigns left Running by a restart carry on


@app.get("/health")
def health():
    return {
        "status": "ok",
        "whatsapp_mock": settings.WHATSAPP_MOCK,
        "whatsapp_provider": "mock" if settings.WHATSAPP_MOCK else "whatsapp",
    }
