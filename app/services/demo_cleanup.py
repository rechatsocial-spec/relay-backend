"""
One-time cleanup of the demo seed (`python -m app.seed`), keeping everything real.

Demo =
  - users whose email ends with @relaycrm.io (the seed's accounts), except PURGE_KEEP_EMAILS
  - numbers that were never connected to the WhatsApp service (the seed's 47 fake numbers)
    and everything attached to them: chats, messages, contacts, campaigns, chatbots
  - automations / API keys / log lines created by demo users in the seed (within minutes of
    the demo accounts being created)
Real numbers (connected accounts), their chats and contacts are KEPT: whatever a demo
user owned is handed to the first real Super Admin before the demo users are deleted.

Safety: nothing happens unless a real (non-demo) Super Admin exists, so you can't lock yourself
out. Use dry_run=True (or `python -m app.purge_demo` without --yes) to see the counts first.
"""
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.base import Base  # noqa: F401  (registers every model; must come before the model imports)
from app.db.session import SessionLocal
from app.models.activity_log import ActivityLog
from app.models.api_key import ApiKey
from app.models.automation import Automation, AutomationRun
from app.models.campaign import Campaign
from app.models.chatbot import Chatbot
from app.models.contact import Contact
from app.models.conversation import Conversation, Message
from app.models.user import Role, User
from app.models.whatsapp_number import WhatsAppNumber

log = logging.getLogger("relay.purge")

DEMO_DOMAIN = "@relaycrm.io"
SEED_WINDOW = timedelta(minutes=5)


def _aware(dt):
    return dt.replace(tzinfo=timezone.utc) if dt is not None and dt.tzinfo is None else dt


def _ids(rows):
    return [r[0] for r in rows]


def purge_demo_data(db: Session, keep_emails=(), dry_run: bool = True) -> dict:
    keep = {e.strip().lower() for e in keep_emails if e.strip()}
    users = db.query(User).all()
    demo = [u for u in users if u.email.lower().endswith(DEMO_DOMAIN) and u.email.lower() not in keep]
    report = {"dry_run": dry_run, "demo_users": len(demo), "error": None}
    if not demo:
        report["note"] = "No demo accounts found — nothing to clean."
        return report
    demo_ids = {u.id for u in demo}
    owners = sorted((u for u in users if u.role == Role.SUPER_ADMIN and u.id not in demo_ids), key=lambda u: _aware(u.created_at))
    if not owners:
        report["error"] = "No real Super Admin exists. Set BOOTSTRAP_ADMIN_EMAIL / BOOTSTRAP_ADMIN_PASSWORD first, then retry."
        return report
    owner = owners[0]
    report["new_owner"] = owner.email

    seed_start = min(_aware(u.created_at) for u in demo)
    seed_end = seed_start + SEED_WINDOW
    in_seed = lambda dt: dt is not None and seed_start - SEED_WINDOW <= _aware(dt) <= seed_end

    demo_numbers = _ids(db.query(WhatsAppNumber.id).filter(WhatsAppNumber.unipile_account_id.is_(None)).all())
    conv_ids = _ids(db.query(Conversation.id).filter(Conversation.whatsapp_number_id.in_(demo_numbers)).all()) if demo_numbers else []
    contact_ids = _ids(db.query(Contact.id).filter(Contact.whatsapp_number_id.in_(demo_numbers)).all()) if demo_numbers else []
    campaign_ids = _ids(db.query(Campaign.id).filter(Campaign.whatsapp_number_id.in_(demo_numbers)).all()) if demo_numbers else []
    auto_ids = [a.id for a in db.query(Automation).filter(Automation.admin_id.in_(demo_ids)).all() if in_seed(a.created_at)]
    key_ids = [k.id for k in db.query(ApiKey).filter(ApiKey.updated_by.in_(demo_ids)).all() if in_seed(k.updated_at)]

    def delete(model, cond):
        return db.query(model).filter(cond).delete(synchronize_session=False)

    n = {}
    n["messages"] = delete(Message, Message.conversation_id.in_(conv_ids)) if conv_ids else 0
    if campaign_ids:
        db.query(Message).filter(Message.campaign_id.in_(campaign_ids)).update({Message.campaign_id: None}, synchronize_session=False)
    if conv_ids or contact_ids or auto_ids:
        for cond in ([AutomationRun.conversation_id.in_(conv_ids)] if conv_ids else []) + \
                    ([AutomationRun.contact_id.in_(contact_ids)] if contact_ids else []) + \
                    ([AutomationRun.automation_id.in_(auto_ids)] if auto_ids else []):
            delete(AutomationRun, cond)
    n["conversations"] = delete(Conversation, Conversation.id.in_(conv_ids)) if conv_ids else 0
    n["contacts"] = delete(Contact, Contact.id.in_(contact_ids)) if contact_ids else 0
    n["campaigns"] = delete(Campaign, Campaign.id.in_(campaign_ids)) if campaign_ids else 0
    n["chatbots"] = delete(Chatbot, Chatbot.whatsapp_number_id.in_(demo_numbers)) if demo_numbers else 0
    n["automations"] = delete(Automation, Automation.id.in_(auto_ids)) if auto_ids else 0
    n["api_keys"] = delete(ApiKey, ApiKey.id.in_(key_ids)) if key_ids else 0
    n["activity_logs"] = delete(ActivityLog, ActivityLog.actor_id.in_(demo_ids))
    n["numbers"] = delete(WhatsAppNumber, WhatsAppNumber.id.in_(demo_numbers)) if demo_numbers else 0

    # whatever real data a demo user still owns moves to the real Super Admin
    moved = {}
    for model, col in ((WhatsAppNumber, "admin_id"), (Contact, "admin_id"), (Conversation, "admin_id"), (Chatbot, "admin_id"),
                       (Campaign, "admin_id"), (Automation, "admin_id"), (ApiKey, "updated_by")):
        c = getattr(model, col)
        moved[model.__tablename__] = db.query(model).filter(c.in_(demo_ids)).update({c: owner.id}, synchronize_session=False)
    n["users"] = delete(User, User.id.in_(demo_ids))
    report.update(deleted=n, reassigned_to_owner={k: v for k, v in moved.items() if v})

    if dry_run:
        db.rollback()
    else:
        db.commit()
    return report


def purge_if_requested() -> None:
    """Startup hook: runs the cleanup once when PURGE_DEMO_DATA=true."""
    if not settings.PURGE_DEMO_DATA:
        return
    db = SessionLocal()
    try:
        report = purge_demo_data(db, settings.PURGE_KEEP_EMAILS.split(","), dry_run=False)
        if report["error"]:
            log.error("Demo cleanup skipped: %s", report["error"])
        else:
            log.warning("Demo cleanup done: %s — remove PURGE_DEMO_DATA now", report)
    except Exception:
        db.rollback()
        log.exception("Demo cleanup failed (nothing was changed)")
    finally:
        db.close()
