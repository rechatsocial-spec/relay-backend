from fastapi import APIRouter

from app.api.v1.endpoints import (
    auth, admins, numbers, contacts, inbox, campaigns,
    automations, chatbot, analytics, api_keys, activity_logs, system, unipile, media,
)

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(admins.router)
api_router.include_router(numbers.router)
api_router.include_router(contacts.router)
api_router.include_router(inbox.router)
api_router.include_router(campaigns.router)
api_router.include_router(automations.router)
api_router.include_router(chatbot.router)
api_router.include_router(analytics.router)
api_router.include_router(api_keys.router)
api_router.include_router(activity_logs.router)
api_router.include_router(system.router)
api_router.include_router(media.router)
api_router.include_router(unipile.router, prefix="/whatsapp")
# Legacy path: webhooks registered before the rename keep working. Hidden from the docs.
api_router.include_router(unipile.router, prefix="/unipile", include_in_schema=False)
