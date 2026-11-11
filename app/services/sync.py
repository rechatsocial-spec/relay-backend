"""
Pulls what is connected at the WhatsApp service into the CRM, so nothing has to be linked by hand:

  1. every WhatsApp account  -> a CRM number (owned by the first real Super Admin;
     a Super Admin can assign it to an Admin)
  2. once per number, its most recent 1:1 chats and their latest messages
     -> contacts, conversations and messages (the unified inbox)
  3. every cycle (SYNC_SECONDS), chats that changed since the last look are re-read, so the
     inbox keeps up even if a webhook delivery is missed

Live events arrive through the webhook (endpoints/unipile.py); this module is the catch-up.
Imports are idempotent (messages are matched by id). `STATE` records the last run and the last
error so the app can show *why* nothing arrived (see /system/whatsapp and POST /numbers/refresh).

If the key is not allowed to list accounts, put the account ids in UNIPILE_ACCOUNT_IDS
(comma separated, "acc_...") and they are read one by one.
"""
import logging
import threading
import time
import uuid
from datetime import datetime, timezone

import httpx
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.automation import AutomationRun
from app.models.contact import Contact, ContactStatus
from app.models.conversation import Conversation, Message, MessageSender, MessageStatus, MessageType
from app.models.user import Role, User
from app.models.whatsapp_number import NumberStatus, WhatsAppNumber
from app.services.chat_utils import (
    attachment_fields, digits_to_phone, find_contact, is_person_chat, is_system_chat, lid_placeholder, phone_from_user_id,
)
from app.services.connection_status import _MAP
from app.services.messaging import get_or_create_conversation, utcnow

log = logging.getLogger("relay.sync")

SYNC_SECONDS = 60
CHAT_PAGE = 50
MAX_CHATS = 200          # per number, first import
MESSAGES_PER_CHAT = 40
PAUSE_SECONDS = 0.2

STATE = {"last_run_at": None, "last_ok_at": None, "error": None, "accounts_found": 0, "imported_chats": 0, "history_importing": False,
         "application_id": None}
_work_lock = threading.Lock()


def _get(path: str, params: dict | None = None, key: str | None = None) -> dict:
    for attempt in (1, 2):
        r = httpx.get(
            settings.UNIPILE_BASE_URL.rstrip("/") + path, params=params, timeout=30,
            headers={"X-API-KEY": key or settings.UNIPILE_API_KEY, "accept": "application/json"},
        )
        if r.status_code == 429 and attempt == 1:  # rate limited: wait once, then retry
            time.sleep(5)
            continue
        break
    r.raise_for_status()
    return r.json()


def explain(exc: Exception) -> str:
    """A sentence a person can act on."""
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        if code == 401 and "expired" in (exc.response.text or "").lower():
            return ("The service answered 401: the API key has EXPIRED (every key has an expiry date). Create a replacement "
                    "with the same access in the service's dashboard, save it on the server, and delete the old one.")
        hint = {
            401: "the API key was rejected — check the key set on the server",
            403: "this key may not list accounts — use a Service Key, or list the account ids on the server",
            404: "address or account not found — check the service address and the account ids on the server",
            429: "rate limited — it will retry",
        }.get(code, "the request failed")
        return f"The WhatsApp service answered {code}: {hint}"
    if isinstance(exc, httpx.HTTPError):
        return f"Could not reach the WhatsApp service ({exc.__class__.__name__})"
    return f"{exc.__class__.__name__}: {str(exc)[:160]}"


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


# ───────────────────────────── accounts -> numbers

def _configured_ids() -> list[str]:
    return [i.strip() for i in settings.UNIPILE_ACCOUNT_IDS.split(",") if i.strip()]


def list_accounts(key: str | None = None) -> list[dict]:
    accounts, offset = [], 0
    try:
        while True:
            body = _get("/accounts", {"provider": "whatsapp", "limit": 100, "offset": offset}, key=key)
            data = body.get("data") or body.get("items") or []
            accounts += data
            if not body.get("has_more") or not data:
                break
            offset += len(data)
    except httpx.HTTPStatusError as e:
        if e.response.status_code in (401, 403, 404) and _configured_ids():
            accounts = []  # listing not allowed: fall back to the ids that were listed by hand
        else:
            raise
    have = {a.get("id") for a in accounts}
    for aid in _configured_ids():
        if aid not in have:
            accounts.append(_get(f"/accounts/{aid}"))
    return [a for a in accounts if a.get("id") and a.get("provider") in (None, "whatsapp")]


def _account_phone(a: dict) -> str | None:
    """The account's own phone number: its user id (a WhatsApp JID), else digits in its name."""
    return phone_from_user_id(a.get("user_id") or "") or digits_to_phone(a.get("name"))


def sync_accounts(db: Session) -> list[WhatsAppNumber]:
    """Makes sure every connected WhatsApp account is a CRM number. Returns the new ones."""
    supers = db.query(User).filter(User.role == Role.SUPER_ADMIN).order_by(User.created_at).all()
    owner = next((u for u in supers if not u.email.lower().endswith("@relaycrm.io")), supers[0] if supers else None)  # not the demo account
    if not owner:
        raise RuntimeError("No Super Admin exists yet — cannot import numbers")
    accounts = list_accounts()
    STATE["accounts_found"] = len(accounts)
    STATE["application_id"] = next((a.get("application_id") for a in accounts if a.get("application_id")), STATE["application_id"])

    created = []
    for a in accounts:
        aid = a["id"]
        phone = _account_phone(a)
        name = (a.get("name") or "").strip()
        label = name or phone or aid
        status = _MAP.get(a.get("status"), ("", NumberStatus.ACTIVE))[1]
        number = db.query(WhatsAppNumber).filter(WhatsAppNumber.unipile_account_id == aid).first()
        if not number and phone:
            number = db.query(WhatsAppNumber).filter(WhatsAppNumber.phone == phone, WhatsAppNumber.unipile_account_id.is_(None)).first()
        if number:
            number.unipile_account_id = aid
            if phone and number.phone != phone and not db.query(WhatsAppNumber).filter(WhatsAppNumber.phone == phone).first():
                if number.display_name == number.phone:
                    number.display_name = label[:120]
                number.phone = phone  # was a placeholder until the real number was known
            if number.status != NumberStatus.INACTIVE:
                number.status = status
        else:
            taken = phone and db.query(WhatsAppNumber).filter(WhatsAppNumber.phone == phone).first()
            number = WhatsAppNumber(phone=(phone if phone and not taken else aid[:30]), display_name=label[:120],
                                    unipile_account_id=aid, admin_id=owner.id, status=status)
            db.add(number)
            created.append(number)
    db.commit()
    return created


# ───────────────────────────── chats -> contacts, conversations, messages

def _profile(account_id: str, user_id: str) -> dict:
    """Best-effort profile of a person (name and, when WhatsApp shares it, the phone as public_identifier)."""
    try:
        return _get(f"/{account_id}/users/{user_id}") or {}
    except Exception:
        return {}


def purge_system_chats(db: Session) -> int:
    """Removes chats an earlier version imported that are DEFINITELY not customers (WhatsApp's own '+0' chat, groups,
    status). Safety: a chat in which anyone ever wrote from this app is never removed, and an id in an unknown
    format is never treated as a system chat."""
    removed = 0
    for conv in db.query(Conversation).filter(Conversation.provider_chat_id.isnot(None)).all():
        if not is_system_chat(conv.provider_chat_id):
            continue
        if db.query(Message).filter(Message.conversation_id == conv.id, Message.sender == MessageSender.BUSINESS).first():
            continue  # someone replied here: keep it, whatever the id looks like
        db.query(AutomationRun).filter(AutomationRun.conversation_id == conv.id).delete(synchronize_session=False)
        db.query(Message).filter(Message.conversation_id == conv.id).delete(synchronize_session=False)
        contact_id = conv.contact_id
        log.warning("Removing system chat %s", conv.provider_chat_id)
        db.delete(conv)
        db.flush()
        if not db.query(Conversation).filter(Conversation.contact_id == contact_id).first():
            db.query(AutomationRun).filter(AutomationRun.contact_id == contact_id).delete(synchronize_session=False)
            db.query(Contact).filter(Contact.id == contact_id).delete(synchronize_session=False)
        removed += 1
    if removed:
        db.commit()
    return removed


def _import_chat(db: Session, number: WhatsAppNumber, chat: dict) -> int:
    """Imports one chat. Returns how many messages were new."""
    chat_id = chat.get("id")
    acc = number.unipile_account_id
    if not chat_id or not is_person_chat(chat_id) or chat.get("type") in ("group", "channel") or chat.get("is_group") or chat.get("is_channel"):
        return 0
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
        return 0
    items.sort(key=lambda x: x[0])

    # whose chat is this? An existing conversation knows; otherwise the phone (or, if WhatsApp hides it, the chat's own id)
    conv = db.query(Conversation).filter(Conversation.provider_chat_id == chat_id, Conversation.whatsapp_number_id == number.id).first()
    is_new_conv = conv is None
    if conv is not None:
        contact = db.get(Contact, conv.contact_id)
    else:
        user_id = chat.get("user_id") or chat_id
        phone = phone_from_user_id(user_id) or phone_from_user_id(chat_id)
        profile = {}
        if not phone:  # a hidden-number (@lid) person: the phone may be on the profile
            profile = _profile(acc, user_id)
            phone = digits_to_phone(profile.get("public_identifier"))
        key = phone or lid_placeholder(user_id)
        contact = find_contact(db, number, key)
        if contact is None:
            contact = Contact(
                name=(chat.get("name") or profile.get("display_name") or profile.get("first_name") or phone or "WhatsApp contact")[:120],
                phone=key, whatsapp_number_id=number.id, admin_id=number.admin_id,
                status=ContactStatus.REPLIED if any(not m.get("is_sender") for _, m in items) else ContactStatus.CONTACTED,
                tags=["WhatsApp"], source="Existing chat",
            )
            db.add(contact)
            db.flush()
        conv = get_or_create_conversation(db, contact, number)
        is_new_conv = not conv.messages
        conv.provider_chat_id = chat_id
    existing = {pid for (pid,) in db.query(Message.provider_message_id).filter(Message.conversation_id == conv.id) if pid}
    batch_ids = {m["id"] for _, m in items}
    # messages we sent from this app: same text, same moment, but possibly stored under another id
    unmatched_out = [m for m in db.query(Message).filter(Message.conversation_id == conv.id, Message.sender == MessageSender.BUSINESS,
                                                           Message.type != MessageType.NOTE).all()
                     if m.provider_message_id not in batch_ids]

    last_any, last_in, new_inbound, added = None, None, 0, 0
    for ts, m in items:
        last_any = ts
        if not m.get("is_sender"):
            last_in = ts
        if m["id"] in existing:
            continue
        if m.get("is_sender"):
            twin = next((x for x in unmatched_out if (x.text or "") == (m.get("text") or "")
                         and abs((_aware(x.created_at) - ts).total_seconds()) < 180), None)
            if twin is not None:  # the same message, already shown: just adopt the service's id
                unmatched_out.remove(twin)
                twin.provider_message_id = m["id"]
                continue
        atts = m.get("attachments") or []
        first = atts[0] if atts else None
        if m.get("is_sender"):
            sender = MessageSender.BUSINESS
            status = MessageStatus.READ if m.get("is_seen") else MessageStatus.DELIVERED if m.get("is_delivered") else MessageStatus.SENT
        else:
            sender, status = MessageSender.CUSTOMER, MessageStatus.DELIVERED
            new_inbound += 1
        mid = uuid.uuid4()
        fields = {"type": MessageType.TEXT, **attachment_fields(first, mid)}
        db.add(Message(id=mid, conversation_id=conv.id, sender=sender, text=m.get("text") or None,
                       status=status, provider_message_id=m["id"], created_at=ts, **fields))
        added += 1

    if last_any and (conv.last_message_at is None or is_new_conv or last_any > _aware(conv.last_message_at)):
        conv.last_message_at = last_any
    if last_in and (conv.last_inbound_at is None or last_in > _aware(conv.last_inbound_at)):
        conv.last_inbound_at = last_in
    if new_inbound and not is_new_conv:
        conv.unread_count = (conv.unread_count or 0) + new_inbound
    if is_new_conv:
        conv.unread_count = max(int(chat.get("unread_count") or 0), 0)
    if last_any and (contact.last_contact_at is None or last_any > _aware(contact.last_contact_at)):
        contact.last_contact_at = last_any
    return added


def _list_chats(acc: str, wanted: int) -> list[dict]:
    chats, cursor = [], None
    while len(chats) < wanted:
        params = {"type": "1to1", "limit": CHAT_PAGE}
        if cursor:
            params["cursor"] = cursor
        body = _get(f"/{acc}/chats", params)
        data = body.get("data") or []
        if not data and not cursor:  # nothing came back at all: say what the service actually answered
            log.warning("Chat list for %s is empty. Response keys: %s; first 300 chars: %s", acc, sorted(body.keys()), str(body)[:300])
        chats += data
        cursor = body.get("next_cursor")
        if not cursor or not data:
            break
    return chats[:wanted]


def sync_history(db: Session, number: WhatsAppNumber) -> int:
    """First import of one number: its most recent chats. Returns how many chats were imported."""
    chats = _list_chats(number.unipile_account_id, MAX_CHATS)
    imported = failed = 0
    for chat in chats:
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
    if not chats:
        # An empty list is not "done": the account may still be syncing at the service. Retry next cycle.
        log.warning("No chats returned for %s; will try again next cycle", number.phone)
    elif failed <= max(2, len(chats) // 5):
        number.history_synced_at = utcnow()
        db.commit()
    log.warning("Imported %s chats for %s (%s listed, %s failed)", imported, number.phone, len(chats), failed)
    return imported


def _recent(db: Session, number: WhatsAppNumber, force_top: int = 0) -> tuple[int, int]:
    """Re-reads recent chats. A chat is re-read when its last message is newer than what we have; the `force_top`
    most recent chats are always re-read (used by 'Retrieve messages' so nothing hides behind a missing date).
    Returns (chats that changed, new messages)."""
    known = {cid: last for cid, last in db.query(Conversation.provider_chat_id, Conversation.last_message_at).filter(
        Conversation.whatsapp_number_id == number.id, Conversation.provider_chat_id.isnot(None))}
    chats_changed = new_messages = 0
    for i, chat in enumerate(_list_chats(number.unipile_account_id, CHAT_PAGE)):
        ts = _ts(chat.get("last_message_timestamp") or (chat.get("last_message") or {}).get("timestamp"))
        last = known.get(chat.get("id"))
        if i >= force_top and last is not None and (ts is None or ts <= _aware(last)):
            continue  # known chat with nothing newer (or no date to compare): leave it to the webhook
        try:
            added = _import_chat(db, number, chat)
            if added:
                chats_changed += 1
                new_messages += added
            db.commit()
        except Exception:
            db.rollback()
            log.exception("Could not refresh chat %s of %s", chat.get("id"), number.phone)
    return chats_changed, new_messages


def sync_recent(db: Session, number: WhatsAppNumber) -> int:
    """Catch-up: re-reads only the chats whose last message is newer than what we have."""
    return _recent(db, number)[0]


def retrieve_recent_messages(number_ids: list | None, wait_seconds: float = 20, max_seconds: float = 55, force_top: int = 6) -> dict:
    """'Retrieve messages': asks the service for the latest messages of these numbers right now and says what it found.
    number_ids=None means every connected number."""
    if not _is_live():
        return {"ok": False, "error": "WhatsApp is not connected on the server: the API key is missing."}
    if not _work_lock.acquire(timeout=wait_seconds):
        return {"ok": True, "busy": True, "numbers": 0, "chats": 0, "new_messages": 0, "errors": [],
                "message": "Messages are already being loaded in the background. Try again in a moment."}
    db = SessionLocal()
    started = time.time()
    result = {"ok": True, "busy": False, "numbers": 0, "chats": 0, "new_messages": 0, "errors": []}
    try:
        q = db.query(WhatsAppNumber).filter(WhatsAppNumber.unipile_account_id.isnot(None))
        if number_ids is not None:
            q = q.filter(WhatsAppNumber.id.in_(number_ids))
        for n in q.order_by(WhatsAppNumber.created_at).all():
            if time.time() - started > max_seconds:
                result["errors"].append({"number": n.display_name, "error": "Ran out of time before this number. Press again."})
                continue
            try:
                chats, msgs = _recent(db, n, force_top=force_top)
                result["numbers"] += 1
                result["chats"] += chats
                result["new_messages"] += msgs
            except Exception as e:
                db.rollback()
                result["errors"].append({"number": n.display_name, "error": explain(e)})
        return result
    finally:
        db.close()
        _work_lock.release()


# ───────────────────────────── orchestration

def _is_live() -> bool:
    return not settings.WHATSAPP_MOCK and bool(settings.UNIPILE_API_KEY)


def _accounts_step() -> dict:
    """Account import. Never raises: the outcome is recorded in STATE."""
    STATE["last_run_at"] = utcnow().isoformat()
    db = SessionLocal()
    try:
        created = sync_accounts(db)
        if STATE["accounts_found"] == 0:
            STATE["error"] = ("Connected to the WhatsApp service, but it lists no WhatsApp accounts for this API key. Make sure the key belongs "
                              "to the application that has your accounts, and is a Service Key (or list the account ids on the server).")
            return {"ok": False, "error": STATE["error"], "accounts_found": 0}
        STATE.update(error=None, last_ok_at=utcnow().isoformat())
        return {"ok": True, "created": len(created), "accounts_found": STATE["accounts_found"]}
    except Exception as e:
        db.rollback()
        STATE["error"] = explain(e)
        log.error("Account import failed: %s", STATE["error"])
        return {"ok": False, "error": STATE["error"], "accounts_found": STATE["accounts_found"]}
    finally:
        db.close()


def _chats_step() -> None:
    """History for numbers not imported yet, catch-up for the others. One at a time."""
    if not _work_lock.acquire(blocking=False):
        return
    STATE["history_importing"] = True
    db = SessionLocal()
    try:
        try:
            purge_system_chats(db)
        except Exception:
            db.rollback()
            log.exception("Could not remove non-customer chats")
        for n in db.query(WhatsAppNumber).filter(WhatsAppNumber.unipile_account_id.isnot(None)).all():
            try:
                if n.history_synced_at is not None and not db.query(Conversation).filter(Conversation.whatsapp_number_id == n.id).first():
                    n.history_synced_at = None  # marked as imported but nothing is here: import again
                    db.commit()
                if n.history_synced_at is None:
                    STATE["imported_chats"] += sync_history(db, n)
                else:
                    STATE["imported_chats"] += sync_recent(db, n)
            except Exception as e:
                db.rollback()
                STATE["error"] = f"{n.phone}: {explain(e)}"
                log.error("Chat sync failed for %s: %s", n.phone, STATE["error"])
    finally:
        db.close()
        STATE["history_importing"] = False
        _work_lock.release()


def sync_all() -> dict | None:
    if not _is_live():
        return None
    report = _accounts_step()
    if report["ok"]:
        _chats_step()
    return report


def refresh_now(full: bool = False) -> dict:
    """Super Admin 'Refresh': accounts right now (so the answer is real), chats in the background.
    full=True also marks every number as not yet imported, so all its chats are read again from the service
    (nothing is deleted; messages already here are matched by id and skipped)."""
    if not _is_live():
        return {"ok": False, "error": "WhatsApp is not connected on the server: the API key is missing."}
    report = _accounts_step()
    if full and report["ok"]:
        db = SessionLocal()
        try:
            db.query(WhatsAppNumber).filter(WhatsAppNumber.unipile_account_id.isnot(None)).update({WhatsAppNumber.history_synced_at: None})
            db.commit()
        finally:
            db.close()
        report["reimport"] = True
    if report["ok"] and not _work_lock.locked():
        threading.Thread(target=_chats_step, daemon=True, name="chat-sync-now").start()
    report["history_importing"] = True
    return report


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
                log.exception("Sync cycle failed (will retry)")
            time.sleep(SYNC_SECONDS)

    threading.Thread(target=loop, daemon=True, name="account-sync").start()
