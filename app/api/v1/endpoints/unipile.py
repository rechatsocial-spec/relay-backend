"""
Unipile v2 WhatsApp integration (the only provider).

Setup, once, in the Unipile dashboard:
  1. Create a Service Key         -> UNIPILE_API_KEY (X-API-KEY; keys expire — rotate)
  2. Add a webhook endpoint       -> {PUBLIC_BASE_URL}/api/v1/unipile/webhook
     events: message.new, message.receipt.delivery, message.receipt.read,
             account.status.*, account.remove
     copy its signing secret      -> UNIPILE_WEBHOOK_SECRET

Already linked it in the dashboard? POST /api/v1/unipile/accounts {account_id, admin_id}.
Linking a number (Super Admin):
  POST /api/v1/unipile/link  {admin_id, display_name}  -> {"link": "https://auth.unipile.com/…"}
  Open the link, scan the QR code / enter the pairing code in WhatsApp
  (Linked devices). Unipile then redirects the browser to
  /api/v1/unipile/callback, which creates the WhatsApp Number in the CRM.

Webhooks carry `unipile-signature: t=<unix>,v0=<hmac_sha256(secret, "t.rawbody")>`.
"""
import hashlib
import hmac
import logging
import re
import time
import uuid
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from jose import JWTError, jwt
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import require_super_admin
from app.core.config import settings
from app.db.session import get_db
from app.models.activity_log import ActivityLog
from app.models.campaign import Campaign
from app.models.contact import Contact, ContactStatus
from app.models.conversation import ConversationStatus, Message, MessageSender, MessageStatus, MessageType
from app.models.user import User
from app.models.whatsapp_number import NumberStatus, WhatsAppNumber
from app.services.automation_runner import start_new_customer_automations
from app.services.messaging import get_or_create_conversation, utcnow
from app.services.whatsapp_service import to_e164

log = logging.getLogger("relay.unipile")
router = APIRouter(prefix="/unipile", tags=["unipile"])

STOP_WORDS = {"STOP", "STOPALL", "UNSUBSCRIBE", "CANCEL", "END", "QUIT"}
START_WORDS = {"START", "UNSTOP", "SUBSCRIBE"}

# Receipts can arrive out of order; never move a message "backwards".
STATUS_RANK = {
    MessageStatus.QUEUED: 0, MessageStatus.SENT: 1, MessageStatus.DELIVERED: 2,
    MessageStatus.UNDELIVERED: 3, MessageStatus.FAILED: 3, MessageStatus.READ: 4,
}


def _find_contact(db: Session, number: WhatsAppNumber, address: str) -> Contact | None:
    target = to_e164(address)
    candidates = db.query(Contact).filter(Contact.admin_id == number.admin_id).all()
    matches = [c for c in candidates if to_e164(c.phone) == target]
    matches.sort(key=lambda c: c.whatsapp_number_id != number.id)  # same number first
    return matches[0] if matches else None


def _message_type(content_type: str | None) -> MessageType:
    ct = (content_type or "").lower()
    if ct == "image/webp":
        return MessageType.STICKER
    if ct.startswith("image/"):
        return MessageType.IMAGE
    if ct.startswith("video/"):
        return MessageType.VIDEO
    if ct.startswith("audio/"):
        return MessageType.VOICE
    return MessageType.TEXT

SIGNATURE_TOLERANCE_SECONDS = 300
ACCOUNT_STATUS = {
    "running": NumberStatus.ACTIVE,
    "degraded": NumberStatus.WARNING,
    "partial": NumberStatus.WARNING,
    "errored": NumberStatus.ERROR,
    "disconnected": NumberStatus.ERROR,
}


def _headers() -> dict:
    if not settings.UNIPILE_API_KEY:
        raise HTTPException(503, "UNIPILE_API_KEY is not configured")
    return {"X-API-KEY": settings.UNIPILE_API_KEY, "accept": "application/json"}


def _api(path: str) -> str:
    return settings.UNIPILE_BASE_URL.rstrip("/") + path


def _public_base() -> str:
    if not settings.PUBLIC_BASE_URL:
        raise HTTPException(503, "PUBLIC_BASE_URL must be set so Unipile can redirect back to this backend")
    return settings.PUBLIC_BASE_URL.rstrip("/")


# ── Linking ──────────────────────────────────────────────────────────────────

class LinkRequest(BaseModel):
    admin_id: uuid.UUID
    display_name: str | None = None


@router.post("/link", dependencies=[Depends(require_super_admin)])
def create_link(payload: LinkRequest, db: Session = Depends(get_db)):
    """Returns a Hosted Auth URL. The Admin who will own the number is carried
    in a signed `state`, so the callback can't be tampered with."""
    if not db.get(User, payload.admin_id):
        raise HTTPException(404, "Admin not found")
    state = jwt.encode(
        {"admin_id": str(payload.admin_id), "display_name": payload.display_name,
         "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
        settings.SECRET_KEY, algorithm=settings.ALGORITHM,
    )
    body = {
        "providers": "whatsapp",
        "redirect_uri": f"{_public_base()}{settings.API_V1_PREFIX}/unipile/callback",
        "expires_on": (datetime.now(timezone.utc) + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "state": state,
        "config": {"whatsapp": {"allow_methods": ["qr", "pairing"]}},
    }
    try:
        r = httpx.post(_api("/auth/link"), json=body, headers=_headers(), timeout=30)
    except httpx.HTTPError as e:
        raise HTTPException(502, f"Could not reach Unipile: {e}")
    if r.status_code >= 300:
        log.warning("Unipile auth/link failed (%s): %s", r.status_code, r.text[:300])
        raise HTTPException(502, f"Unipile rejected the request ({r.status_code})")
    data = r.json()
    link = data.get("link") or (data.get("data") or {}).get("link")
    if not link:
        raise HTTPException(502, "Unipile response had no link")
    return {"link": link}


class RegisterRequest(BaseModel):
    account_id: str
    admin_id: uuid.UUID
    display_name: str | None = None


@router.post("/accounts", dependencies=[Depends(require_super_admin)])
def register_account(payload: RegisterRequest, db: Session = Depends(get_db)):
    """Use an account you already linked in the Unipile dashboard (Accounts > Link
    Account > WhatsApp): paste its account id ("acc_...") to add it as a CRM number."""
    if not db.get(User, payload.admin_id):
        raise HTTPException(404, "Admin not found")
    n = _register_account(db, payload.account_id.strip(), payload.admin_id, payload.display_name)
    return {"id": str(n.id), "phone": n.phone, "unipile_account_id": n.unipile_account_id, "status": n.status.value}


def _register_account(db: Session, account_id: str, admin_id: uuid.UUID, display_name: str | None) -> WhatsAppNumber:
    """Fetches the Unipile account and creates/updates the matching CRM number."""
    try:
        r = httpx.get(_api(f"/accounts/{account_id}"), headers=_headers(), timeout=30)
    except httpx.HTTPError as e:
        raise HTTPException(502, f"Could not reach Unipile: {e}")
    if r.status_code == 404:
        raise HTTPException(404, "Unipile account not found for this API key")
    if r.status_code >= 300:
        raise HTTPException(502, f"Could not fetch Unipile account ({r.status_code})")
    account = r.json()
    if account.get("provider") not in (None, "whatsapp"):
        raise HTTPException(400, f"Account is {account.get('provider')}, not WhatsApp")

    # Account.name is "usually the public identifier" — for WhatsApp, the phone number.
    digits = re.sub(r"\D", "", account.get("name") or "")
    phone = f"+{digits}" if digits else account_id[:30]
    status = ACCOUNT_STATUS.get(account.get("status"), NumberStatus.ACTIVE)

    number = db.query(WhatsAppNumber).filter(WhatsAppNumber.unipile_account_id == account_id).first()
    if not number:
        number = db.query(WhatsAppNumber).filter(WhatsAppNumber.phone == phone).first()
    if number:
        number.unipile_account_id = account_id
        number.status = status
    else:
        number = WhatsAppNumber(
            phone=phone, display_name=display_name or phone,
            unipile_account_id=account_id, admin_id=admin_id, status=status,
        )
        db.add(number)
    db.add(ActivityLog(type="whatsapp_number", message=f"WhatsApp number {phone} linked via Unipile"))
    db.commit()
    db.refresh(number)
    return number


@router.get("/callback")
def link_callback(
    account_id: str | None = None, state: str | None = None,
    error_type: str | None = None, error_title: str | None = None, error_detail: str | None = None,
    db: Session = Depends(get_db),
):
    """Unipile redirects the user's browser here after linking."""
    def finish(ok: bool, msg: str = ""):
        if settings.FRONTEND_URL:
            q = "unipile=linked" if ok else f"unipile=error&detail={msg[:100]}"
            return RedirectResponse(f"{settings.FRONTEND_URL.rstrip('/')}/?{q}")
        return {"ok": ok, "detail": msg}

    if error_type or not account_id:
        return finish(False, error_title or error_detail or error_type or "Linking failed")
    try:
        claims = jwt.decode(state or "", settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        admin_id = uuid.UUID(claims["admin_id"])
    except (JWTError, KeyError, ValueError):
        raise HTTPException(400, "Invalid or expired state")

    _register_account(db, account_id, admin_id, claims.get("display_name"))
    return finish(True)


# ── Webhook ──────────────────────────────────────────────────────────────────

def _verify_signature(raw: bytes, header: str) -> None:
    if not settings.UNIPILE_WEBHOOK_SECRET:
        raise HTTPException(503, "UNIPILE_WEBHOOK_SECRET is not configured")
    parts = dict(p.split("=", 1) for p in header.split(",") if "=" in p)
    t, sig = parts.get("t", ""), parts.get("v0", "")
    if not t.isdigit() or abs(time.time() - int(t)) > SIGNATURE_TOLERANCE_SECONDS:
        raise HTTPException(403, "Stale or missing signature timestamp")
    expected = hmac.new(settings.UNIPILE_WEBHOOK_SECRET.encode(), f"{t}.".encode() + raw, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig):
        log.warning("Rejected Unipile webhook with bad signature")
        raise HTTPException(403, "Invalid Unipile signature")


def _phone_from_user_id(user_id: str) -> str | None:
    """'919820011234@s.whatsapp.net' -> '+919820011234'. '@lid' ids hide the number."""
    if not user_id.endswith("@s.whatsapp.net"):
        return None
    digits = re.sub(r"\D", "", user_id.split("@", 1)[0])
    return f"+{digits}" if digits else None


def _digits_to_phone(value: str | None) -> str | None:
    digits = re.sub(r"\D", "", value or "")
    return f"+{digits}" if len(digits) >= 7 else None


def _resolve_phone(number: WhatsAppNumber, m: dict) -> str | None:
    """Phone of the sender. WhatsApp may identify people by an @lid id; then the
    phone is in sender.public_identifier, or in the user profile (GET /{account}/users/{id})."""
    sender_id = m.get("sender_id") or ""
    phone = _phone_from_user_id(sender_id)
    if phone:
        return phone
    phone = _digits_to_phone((m.get("sender") or {}).get("public_identifier"))
    if phone or not sender_id or not number.unipile_account_id:
        return phone
    try:
        r = httpx.get(_api(f"/{number.unipile_account_id}/users/{sender_id}"), headers=_headers(), timeout=15)
        if r.status_code < 300:
            return _digits_to_phone(r.json().get("public_identifier"))
    except (httpx.HTTPError, HTTPException, ValueError):
        log.warning("Could not look up profile for %s", sender_id)
    return None


def _handle_message_new(db: Session, number: WhatsAppNumber, m: dict) -> None:
    if m.get("is_sender") or m.get("is_event"):
        return  # our own message (sent via the API or the phone) / a system event
    mid = m.get("id")
    if mid and db.query(Message).filter(Message.provider_message_id == mid).first():
        return  # webhook retry
    chat_id = m.get("chat_id") or ""
    if chat_id.endswith("@g.us"):
        return  # group chats aren't 1:1 CRM conversations
    sender_id = m.get("sender_id") or ""
    phone = _resolve_phone(number, m)
    if not phone:
        db.add(ActivityLog(type="whatsapp_number", message=f"Ignored message from {sender_id} — Unipile did not expose a phone number for it"))
        db.commit()
        return

    name = ((m.get("sender") or {}).get("display_name") or phone)[:120]
    contact = _find_contact(db, number, phone)
    is_new_customer = contact is None
    if not contact:
        contact = Contact(
            name=name, phone=phone, whatsapp_number_id=number.id, admin_id=number.admin_id,
            status=ContactStatus.NEW, tags=["WhatsApp inbound"], source="Inbound WhatsApp",
        )
        db.add(contact)
        db.flush()

    conv = get_or_create_conversation(db, contact, number)
    if chat_id:
        conv.provider_chat_id = chat_id  # reply to the id Unipile uses, unchanged (may be @lid)
    previous = (
        db.query(Message).filter(Message.conversation_id == conv.id)
        .order_by(Message.created_at.desc()).first()
    )

    attachments = m.get("attachments") or []
    first = attachments[0] if attachments else None
    body = m.get("text") or None
    mtype = MessageType.TEXT
    if first:
        mtype = MessageType.STICKER if (first.get("type") == "img" and first.get("sticker")) else _message_type(first.get("mimetype"))
    db.add(Message(
        conversation_id=conv.id, sender=MessageSender.CUSTOMER, type=mtype, text=body,
        media_url=(first or {}).get("url"), status=MessageStatus.DELIVERED, provider_message_id=mid,
    ))

    keyword = (body or "").strip().upper()
    if keyword in STOP_WORDS and not contact.opted_out:
        contact.opted_out = True
        db.add(Message(conversation_id=conv.id, sender=MessageSender.SYSTEM, type=MessageType.NOTE,
                       text="Contact opted out (STOP) — campaigns will skip them", status=MessageStatus.SENT))
    elif keyword in START_WORDS and contact.opted_out:
        contact.opted_out = False
        db.add(Message(conversation_id=conv.id, sender=MessageSender.SYSTEM, type=MessageType.NOTE,
                       text="Contact opted back in (START)", status=MessageStatus.SENT))

    if previous and previous.sender == MessageSender.BUSINESS and previous.campaign_id:
        campaign = db.get(Campaign, previous.campaign_id)
        if campaign:
            campaign.replies += 1

    now = utcnow()
    if contact.status in (ContactStatus.NEW, ContactStatus.CONTACTED):
        contact.status = ContactStatus.REPLIED
    contact.last_contact_at = now
    conv.last_inbound_at = now
    conv.last_message_at = now
    conv.unread_count = (conv.unread_count or 0) + 1
    if conv.status in (ConversationStatus.BOT, ConversationStatus.HUMAN):
        conv.status = ConversationStatus.NEEDS_HUMAN
    number.last_activity_at = now
    if is_new_customer and not contact.opted_out:
        start_new_customer_automations(db, number, contact, conv)  # sent by the poller within seconds
    db.commit()


def _handle_receipt(db: Session, message_id: str | None, new: MessageStatus) -> None:
    message = db.query(Message).filter(Message.provider_message_id == message_id).first() if message_id else None
    if not message:
        return
    old = message.status
    if STATUS_RANK.get(new, 0) <= STATUS_RANK.get(old, 0):
        return
    message.status = new
    if message.campaign_id:
        campaign = db.get(Campaign, message.campaign_id)
        if campaign:
            if new in (MessageStatus.DELIVERED, MessageStatus.READ) and old not in (MessageStatus.DELIVERED, MessageStatus.READ):
                campaign.delivered += 1
            if new == MessageStatus.READ:
                campaign.read += 1
    db.commit()


@router.post("/webhook", status_code=200)
async def unipile_webhook(request: Request, db: Session = Depends(get_db)):
    raw = await request.body()
    _verify_signature(raw, request.headers.get("unipile-signature", ""))
    try:
        event = await request.json()
    except Exception:
        raise HTTPException(400, "Invalid JSON")

    etype = event.get("type", "")
    payload = event.get("payload") or {}
    account_id = event.get("account_id")
    number = db.query(WhatsAppNumber).filter(WhatsAppNumber.unipile_account_id == account_id).first() if account_id else None

    if not number:
        log.info("Unipile event %s for unknown account %s", etype, account_id)
        return Response(status_code=200)

    if etype == "message.new":
        _handle_message_new(db, number, payload)
    elif etype == "message.receipt.delivery":
        _handle_receipt(db, payload.get("message_id"), MessageStatus.DELIVERED)
    elif etype == "message.receipt.read":
        _handle_receipt(db, payload.get("message_id"), MessageStatus.READ)
    elif etype.startswith("account.status."):
        new = ACCOUNT_STATUS.get(etype.rsplit(".", 1)[-1])
        if new and number.status != new:
            number.status = new
            db.add(ActivityLog(type="whatsapp_number", message=f"WhatsApp number {number.phone} is now {new.value} ({etype})"))
            db.commit()
    elif etype == "account.remove":
        number.status = NumberStatus.INACTIVE
        db.commit()
    return Response(status_code=200)
