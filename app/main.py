from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.db.base import Base
from app.db.session import engine
from app.db.auto_migrate import upgrade_schema
from app.services.campaign_runner import resume_interrupted_campaigns
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
    resume_interrupted_campaigns()  # campaigns left Running by a restart carry on


@app.get("/health")
def health():
    return {
        "status": "ok",
        "whatsapp_mock": settings.WHATSAPP_MOCK,
        "whatsapp_provider": "mock" if settings.WHATSAPP_MOCK else settings.WHATSAPP_PROVIDER,
    }
