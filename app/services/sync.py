"""
Imports what is already connected at the WhatsApp provider, so nothing has to be
linked or added by hand:

  1. every WhatsApp account  -> a CRM number (owned by the first Super Admin;
     a Super Admin can reassign it to an Admin)
  2. once per number, its most recent 1:1 chats and their latest messages
     -> contacts, conversations and messages (the unified inbox)

After that, live messages arrive through the webhook (endpoints/unipile.py).
Runs in a background thread: once at startup, then every SYNC_SECONDS (picks up
newly connected accounts). Imports are idempotent (messages are matched by id).
"""
import logging
import threading
import time
from datetime import datetime, timezone

import httpx
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.contact import Contact, ContactStatus
from app.models.conversation import Conversation, Message, MessageSender, MessageStatus, MessageType
from app.models.user import Role, User
from app.models.whatsapp_number import NumberStatus, WhatsAppNumber
from app.services.chat_utils import digits_to_phone, find_contact, message_type, phone_from_user_id
from app.services.connection_status import _MAP
from app.services.messaging import get_or_create_conversation, utcnow

log = logging.getLogger("relay.sync")

SYNC_SECONDS = 60
CHAT_PAGE = 50
MAX_CHATS = 100          # per number
MESSAGES_PER_CHAT = 30
PAUSE_SECONDS = 0.2


def _get(path: str, params: dict | None = None) -> dict:
    for attempt in (1, 2):
        r = httpx.get(
            settings.UNIPILE_BASE_URL.rstrip("/") + path, params=params, timeout=30,
            headers={"X-API-KEY": settings.UNIPILE_API_KEY, "accept": "application/json"},
        )
        if r.status_code == 429 and attempt == 1:  # rate limited: wait once, then retry
            time.sleep(5)
            continue
        break
    r.raise_for_status()
    return r.json()


def _ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _aware(dt: datetime | None) -> datetime | None:
    return dt.replace(tzinfo=timezone.utc) if dt is not None and dt.tzinfo is None else dt  # SQLite is naive


def sync_accounts(db: Session) -> list[WhatsAppNumber]:
    """Makes sure every connected WhatsApp account is a CRM number. Returns the new ones."""
    supers = db.query(User).filter(User.role == Role.SUPER_ADMIN).order_by(User.created_at).all()
    owner = next((u for u in supers if not u.email.lower().endswith("@relaycrm.io")), supers[0] if supers else None)  # not the demo account
    if not owner:
        log.warning("No Super Admin exists yet — cannot import numbers")
        return []
    accounts, offset = [], 0
    while True:
        body = _get("/accounts", {"provider": "whatsapp", "limit": 100, "offset": offset})
        data = body.get("data") or []
        accounts += data
        if not body.get("has_more") or not data:
            break
        offset += len(data)

    created = []
    for a in accounts:
        aid = a.get("id")
        if not aid:
            continue
        phone = digits_to_phone(a.get("name")) or aid[:30]
        status = _MAP.get(a.get("status"), ("", NumberStatus.ACTIVE))[1]
        number = db.query(WhatsAppNumber).filter(WhatsAppNumber.unipile_account_id == aid).first()
        if not number:
            number = db.query(WhatsAppNumber).filter(WhatsAppNumber.phone == phone).first()
        if number:
            number.unipile_account_id = aid
            if number.status != NumberStatus.INACTIVE:
                number.status = status
        else:
            number = WhatsAppNumber(phone=phone, display_name=(a.get("name") or phone)[:120], unipile_account_id=aid,
                                    admin_id=owner.id, status=status)
            db.add(number)
            created.append(number)
    db.commit()
    return created


def _phone_for_chat(account_id: str, chat: dict) -> str | None:
    user_id = chat.get("user_id") or chat.get("id") or ""
    phone = phone_from_user_id(user_id) or phone_from_user_id(chat.get("id") or "")
    if phone:
        return phone
    try:  # an @lid id: the phone number is on the user's profile
        return digits_to_phone(_get(f"/{account_id}/users/{user_id}").get("public_identifier"))
    except Exception:
        return None


def _import_chat(db: Session, number: WhatsAppNumber, chat: dict) -> bool:
    chat_id = chat.get("id")
    acc = number.unipile_account_id
    if not chat_id:
        return False
    phone = _phone_for_chat(acc, chat)
    if not phone:
        return False
    messages = (_get(f"/{acc}/chats/{chat_id}/messages", {"limit": MESSAGES_PER_CHAT}).get("data")) or []
    items = []
    for m in messages:
        ts = _ts(m.get("timestamp"))
        if not m.get("id") or not ts or m.get("is_event") or m.get("is_deleted"):
            continue
        if not (m.get("text") or m.get("attachments")):
            continue
        items.append((ts, m))
    if not items:
        return False
    items.sort(key=lambda x: x[0])

    contact = find_contact(db, number, phone)
    is_new_contact = contact is None
    if is_new_contact:
        contact = Contact(
            name=(chat.get("name") or phone)[:120], phone=phone, whatsapp_number_id=number.id, admin_id=number.admin_id,
            status=ContactStatus.REPLIED if any(not m.get("is_sender") for _, m in items) else ContactStatus.CONTACTED,
            tags=["WhatsApp"], source="Existing chat",
        )
        db.add(contact)
        db.flush()

    conv = db.query(Conversation).filter(Conversation.provider_chat_id == chat_id, Conversation.whatsapp_number_id == number.id).first()
    is_new_conv = conv is None
    if is_new_conv:
        conv = get_or_create_conversation(db, contact, number)
        is_new_conv = not conv.messages
        conv.provider_chat_id = chat_id
    existing = {pid for (pid,) in db.query(Message.provider_message_id).filter(Message.conversation_id == conv.id) if pid}

    last_any, last_in = None, None
    for ts, m in items:
        last_any = ts
        if not m.get("is_sender"):
            last_in = ts
        if m["id"] in existing:
            continue
        atts = m.get("attachments") or []
        first = atts[0] if atts else None
        mtype = MessageType.TEXT
        if first:
            mtype = MessageType.STICKER if (first.get("type") == "img" and first.get("sticker")) else message_type(first.get("mimetype"))
        if m.get("is_sender"):
            sender = MessageSender.BUSINESS
            status = MessageStatus.READ if m.get("is_seen") else MessageStatus.DELIVERED if m.get("is_delivered") else MessageStatus.SENT
        else:
            sender, status = MessageSender.CUSTOMER, MessageStatus.DELIVERED
        db.add(Message(conversation_id=conv.id, sender=sender, type=mtype, text=m.get("text") or None,
                       media_url=(first or {}).get("url"), status=status, provider_message_id=m["id"], created_at=ts))

    if last_any and (conv.last_message_at is None or is_new_conv or last_any > _aware(conv.last_message_at)):
        conv.last_message_at = last_any
    if last_in and (conv.last_inbound_at is None or last_in > _aware(conv.last_inbound_at)):
        conv.last_inbound_at = last_in
    if is_new_conv:
        conv.unread_count = max(int(chat.get("unread_count") or 0), 0)
    if last_any and (contact.last_contact_at is None or last_any > _aware(contact.last_contact_at)):
        contact.last_contact_at = last_any
    return True


def sync_history(db: Session, number: WhatsAppNumber) -> int:
    """Imports the most recent chats of one number. Returns how many chats were imported."""
    acc = number.unipile_account_id
    chats, cursor = [], None
    while len(chats) < MAX_CHATS:
        params = {"type": "1to1", "limit": CHAT_PAGE}
        if cursor:
            params["cursor"] = cursor
        body = _get(f"/{acc}/chats", params)
        data = body.get("data") or []
        chats += data
        cursor = body.get("next_cursor")
        if not cursor or not data:
            break
    imported = failed = 0
    for chat in chats[:MAX_CHATS]:
        try:
            if _import_chat(db, number, chat):
                imported += 1
            db.commit()
        except Exception:
            db.rollback()
            failed += 1
            log.exception("Could not import chat %s of %s", chat.get("id"), number.phone)
        time.sleep(PAUSE_SECONDS)  # stay well inside the provider's rate limits
    # Mostly failed (rate limit, outage)? Leave it unmarked so the next round retries —
    # already-imported messages are skipped, so a retry is cheap.
    if failed <= max(2, len(chats) // 5):
        number.history_synced_at = utcnow()
        db.commit()
    log.warning("Imported %s chats for %s (%s failed)", imported, number.phone, failed)
    return imported


def sync_all() -> None:
    if settings.WHATSAPP_MOCK or not settings.UNIPILE_API_KEY:
        return
    db = SessionLocal()
    try:
        sync_accounts(db)
        pending = db.query(WhatsAppNumber).filter(
            WhatsAppNumber.unipile_account_id.isnot(None), WhatsAppNumber.history_synced_at.is_(None)
        ).all()
        for n in pending:
            try:
                sync_history(db, n)
            except Exception:
                db.rollback()
                log.exception("History import failed for %s (will retry)", n.phone)
    finally:
        db.close()


_started = False


def start_sync_loop() -> None:
    global _started
    if _started:
        return
    _started = True

    def loop():
        while True:
            try:
                sync_all()
            except Exception:
                log.exception("Account sync failed (will retry)")
            time.sleep(SYNC_SECONDS)

    threading.Thread(target=loop, daemon=True, name="account-sync").start()
