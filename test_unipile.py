"""
End-to-end tests for the Unipile WhatsApp integration — no Unipile account or
network needed. A fake HTTP client records every API call, and webhooks are
signed with Unipile's documented signature scheme.

    python test_unipile.py
"""
import hashlib
import hmac
import json
import os
import tempfile
import time
import types
import uuid

db_file = os.path.join(tempfile.mkdtemp(), "unipile_test.db")
SECRET = "whsec_test"
os.environ.update(
    DATABASE_URL=f"sqlite:///{db_file}", SECRET_KEY="test", WHATSAPP_MOCK="false",
    UNIPILE_API_KEY="key_test", UNIPILE_WEBHOOK_SECRET=SECRET,
    PUBLIC_BASE_URL="https://crm.example.com", CAMPAIGN_SEND_INTERVAL_SECONDS="0",
    BOOTSTRAP_ADMIN_EMAIL="Boss@Example.com", BOOTSTRAP_ADMIN_PASSWORD="boot-pass-1",
)

import run_dev_sqlite  # noqa: E402  (SQLite type shims)
import sqlalchemy.dialects.postgresql as pg  # noqa: E402
pg.UUID = lambda as_uuid=False, *a, **k: run_dev_sqlite._SqliteUUID()
pg.ARRAY = lambda item_type=None, *a, **k: run_dev_sqlite._SqliteARRAY(item_type)

import httpx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import app.seed as seed  # noqa: E402
import app.services.whatsapp_service as ws  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.models.conversation import Message  # noqa: E402
from app.models.whatsapp_number import WhatsAppNumber  # noqa: E402


class FakeHttp:
    """Stands in for httpx.Client in the sending service."""

    def __init__(self):
        self.calls = []
        self.fail = False
        self.new_chat = False

    def post(self, url, json=None, headers=None):
        self.calls.append({"url": url, "json": json, "headers": headers})
        if self.new_chat and "/messages/send" in url:
            return httpx.Response(404, json={"detail": "chat not found"})
        if self.fail:
            return httpx.Response(422, json={"detail": "Recipient is not on WhatsApp"})
        return httpx.Response(200, json={"object": "ChatStarted", "chat_id": "c1", "message_id": f"msg_{len(self.calls)}"})

    def get(self, url, follow_redirects=True):
        return httpx.Response(200, content=b"\x89PNG-bytes", headers={"content-type": "image/png"},
                              request=httpx.Request("GET", url))


fake = FakeHttp()
ws._unipile_singleton = ws.UnipileWhatsAppService(http=fake)

# Fake provider read API: accounts, chats, messages, profiles.
import re  # noqa: E402
httpx_get = httpx.get
ACCOUNTS = [
    {"id": "acc_123", "name": "+91 98765 43210", "status": "running", "provider": "whatsapp"},
    {"id": "acc_456", "name": "+91 91234 56789", "status": "running", "provider": "whatsapp"},
]
CHATS = {
    "acc_123": [
        {"id": "919811111111@s.whatsapp.net", "name": "Old Friend", "user_id": "919811111111@s.whatsapp.net", "unread_count": 2},
        {"id": "555777@lid", "name": "Lid Chat", "user_id": "555777@lid", "unread_count": 0},
        {"id": "nophone@lid", "name": "No Phone", "user_id": "nophone@lid", "unread_count": 0},
    ],
    "acc_456": [{"id": "919822222222@s.whatsapp.net", "name": "Second Number Customer", "user_id": "919822222222@s.whatsapp.net", "unread_count": 0}],
}
def _m(i, text, ts, sender=False, **kw):
    return {"id": i, "text": text, "timestamp": ts, "is_sender": sender, "attachments": [], "sender_id": "x", **kw}
MESSAGES = {  # newest first, like the real API
    "919811111111@s.whatsapp.net": [
        _m("w3", "Great, see you", "2026-11-10T10:05:00.000Z", True, is_seen=True),
        _m("w2", "Is it still available?", "2026-11-10T10:00:00.000Z"),
        _m("w1", "Hello", "2026-11-10T09:59:00.000Z", True, is_delivered=True),
    ],
    "555777@lid": [_m("l1", "Hi from a hidden-number contact", "2026-11-10T08:00:00.000Z")],
    "nophone@lid": [_m("n1", "who am i", "2026-11-10T07:00:00.000Z")],
    "919822222222@s.whatsapp.net": [_m("s1", "Hello second number", "2026-11-10T11:00:00.000Z")],
}
USERS = {"555777@lid": "+91 98111 00099"}


def provider_get(url, params=None, **kw):
    path = url.split("/v2", 1)[1]
    req = httpx.Request("GET", url)
    R = lambda code, data: httpx.Response(code, json=data, request=req)
    if path == "/accounts":
        return R(200, {"object": "Accounts", "data": ACCOUNTS, "has_more": False})
    if (m := re.fullmatch(r"/(acc_\w+)/chats/(.+)/messages", path)):
        return R(200, {"data": MESSAGES.get(m.group(2), [])})
    if (m := re.fullmatch(r"/(acc_\w+)/chats", path)):
        return R(200, {"data": CHATS.get(m.group(1), [])})
    if (m := re.fullmatch(r"/(acc_\w+)/users/(.+)", path)):
        return R(200, {"public_identifier": USERS[m.group(2)]}) if m.group(2) in USERS else R(404, {})
    if (m := re.fullmatch(r"/accounts/(acc_\w+)", path)):
        acc = next((x for x in ACCOUNTS if x["id"] == m.group(1)), None)
        return R(200, acc) if acc else R(404, {})
    return R(404, {})


httpx.get = provider_get

seed.run()
client = TestClient(app)
from app.bootstrap import ensure_bootstrap_admin  # noqa: E402
passed = failed = 0


def check(cond, label):
    global passed, failed
    passed, failed = passed + bool(cond), failed + (not cond)
    print(("[OK] " if cond else "[FAIL] ") + label)


def login(email):
    t = client.post("/api/v1/auth/login", json={"email": email, "password": "password123"}).json()["access_token"]
    return {"Authorization": f"Bearer {t}"}


def webhook(event, sign=True, ts=None, path="/api/v1/whatsapp/webhook"):
    raw = json.dumps(event).encode()
    t = str(ts or int(time.time()))
    sig = hmac.new(SECRET.encode(), f"{t}.".encode() + raw, hashlib.sha256).hexdigest()
    headers = {"unipile-signature": f"t={t},v0={sig if sign else 'bad'}", "content-type": "application/json"}
    return client.post(path, content=raw, headers=headers)


SA = login("deepa@relaycrm.io")
ADMIN = login("rahul.sharma@relaycrm.io")
rahul_id = client.get("/api/v1/admins/me", headers=ADMIN).json()["id"]

# --- bootstrap admin + removed providers -----------------------------------------
ensure_bootstrap_admin()
ensure_bootstrap_admin()  # idempotent
r = client.post("/api/v1/auth/login", json={"email": "boss@example.com", "password": "boot-pass-1"})
check(r.status_code == 200, "Bootstrap Super Admin can log in (email normalised, created once)")
check(client.post("/api/v1/webhooks/twilio/inbound", data={"x": "1"}).status_code == 404, "Twilio webhook routes are gone")
check(client.get("/health").json() == {"status": "ok", "whatsapp_mock": False, "whatsapp_provider": "whatsapp"}, "/health reports live WhatsApp (no provider name)")

# --- nothing to link: connected accounts are imported -----------------------------
check(all(client.request(m, f"/api/v1/whatsapp/{x}", headers=SA, json={}).status_code == 404
          for m, x in (("POST", "link"), ("POST", "accounts"), ("GET", "callback"))), "Link / register / callback endpoints are gone")

import app.services.sync as sync  # noqa: E402
sync.PAUSE_SECONDS = 0
from app.models.whatsapp_number import WhatsAppNumber  # noqa: E402
before = len(client.get("/api/v1/numbers", headers=SA).json())
with SessionLocal() as db:
    created = sync.sync_accounts(db)
check(len(created) == 2, "Both connected accounts become CRM numbers")
with SessionLocal() as db:
    check(sync.sync_accounts(db) == [], "Syncing again creates no duplicates")
numbers = client.get("/api/v1/numbers", headers=SA).json()
num = next((n for n in numbers if n["phone"] == "+919876543210"), None)
num2 = next((n for n in numbers if n["phone"] == "+919123456789"), None)
check(num and num2 and len(numbers) == before + 2 and num["display_name"] == "+91 98765 43210" and num["admin_id"] != rahul_id,
      "Imported number: phone + name from the account, owned by a Super Admin")
check(client.patch(f"/api/v1/numbers/{num['id']}", headers=SA, json={"admin_id": rahul_id}).status_code == 200
      and client.patch(f"/api/v1/numbers/{num2['id']}", headers=SA, json={"admin_id": rahul_id}).status_code == 200,
      "A Super Admin can hand the numbers to an Admin")

with SessionLocal() as db:
    for acc in ("acc_123", "acc_456"):
        sync.sync_history(db, db.query(WhatsAppNumber).filter(WhatsAppNumber.unipile_account_id == acc).first())
convs = client.get("/api/v1/inbox/conversations", headers=SA).json()
byphone = {c["contact"]["phone"]: c for c in convs}
check("+919811111111" in byphone and "+919822222222" in byphone, "Existing chats of BOTH numbers appear in one unified inbox")
check("+919811100099" in byphone and byphone["+919811100099"]["contact"]["name"] == "Lid Chat", "A hidden-number (@lid) chat is resolved through the profile")
check(not any(c["contact"]["name"] == "No Phone" for c in convs), "A chat whose phone can't be resolved is skipped")
check({byphone["+919811111111"]["whatsapp_number_phone"], byphone["+919822222222"]["whatsapp_number_phone"]} == {"+919876543210", "+919123456789"}
      and byphone["+919822222222"]["whatsapp_number_name"], "Each conversation says which number it belongs to")
only = client.get("/api/v1/inbox/conversations", headers=SA, params={"number_id": num2["id"]}).json()
check(only and all(c["whatsapp_number_id"] == num2["id"] for c in only) and len(only) < len(convs), "Filtering by number shows only that number's chats")
stats = next(n for n in client.get("/api/v1/numbers", headers=SA).json() if n["id"] == num["id"])
check(stats["chats"] == 2 and stats["unread"] == 2, "Numbers list shows chats + unread per number")
d = client.get(f"/api/v1/inbox/conversations/{byphone['+919811111111']['id']}", headers=SA).json()
check(next(n for n in client.get('/api/v1/numbers', headers=SA).json() if n['id'] == num['id'])['unread'] == 0, 'Opening a chat marks it read')
check([m["text"] for m in d["messages"]] == ["Hello", "Is it still available?", "Great, see you"], "History is in order, oldest first")
check([m["sender"] for m in d["messages"]] == ["business", "customer", "business"] and [m["status"] for m in d["messages"]] == ["delivered", "delivered", "read"],
      "Sent / received and read states are kept")
check(byphone["+919811111111"]["unread_count"] == 2, "Unread count comes from the chat")
check(byphone["+919811111111"]["last_inbound_at"].startswith("2026-11-10T10:00"), "Last customer message time is set (reply window)")
with SessionLocal() as db:
    n_before = db.query(Message).count()
    sync.sync_history(db, db.query(WhatsAppNumber).filter(WhatsAppNumber.unipile_account_id == "acc_123").first())
    check(db.query(Message).count() == n_before, "Importing history twice does not duplicate messages")
sync.sync_all()  # nothing pending: must be a quiet no-op
reply = client.post(f"/api/v1/inbox/conversations/{byphone['+919822222222']['id']}/messages", headers=SA, json={"type": "text", "text": "hi from second"})
check(fake.calls[-1]["url"].endswith("/acc_456/chats/919822222222@s.whatsapp.net/messages/send") and reply.json()["status"] == "sent",
      "A reply goes out from the number the chat belongs to")

st = client.get("/api/v1/system/whatsapp", headers=ADMIN).json()
check(st["provider"] == "whatsapp" and st["credentials_set"] and st["inbound_webhook_url"] == "https://crm.example.com/api/v1/whatsapp/webhook", "/system/whatsapp reports live + neutral webhook URL")

# --- inbound ------------------------------------------------------------------
def inbound(mid, text="Hi, is the sale still on?", sender="919876500001@s.whatsapp.net", **extra):
    return webhook({"type": "message.new", "account_id": "acc_123",
                    "payload": {"id": mid, "chat_id": sender, "sender_id": sender, "text": text, "is_sender": False,
                                "sender": {"display_name": "Asha Rao"}, "attachments": [], **extra}})


check(inbound("m_in_1").status_code == 200, "Inbound webhook accepted")
convs = client.get("/api/v1/inbox/conversations", headers=ADMIN).json()
conv = next((c for c in convs if c["contact"]["phone"] == "+919876500001"), None)
check(conv and conv["contact"]["name"] == "Asha Rao" and conv["unread_count"] == 1 and conv["status"] == "needs_human",
      "Unknown sender becomes a contact; conversation needs a human")
inbound("m_in_1")
detail = client.get(f"/api/v1/inbox/conversations/{conv['id']}", headers=ADMIN).json()
check(sum(m["text"] == "Hi, is the sale still on?" for m in detail["messages"]) == 1, "Webhook retries are de-duplicated")

check(webhook({"type": "message.new", "account_id": "acc_123", "payload": {"id": "x"}}, sign=False).status_code == 403, "Bad signature rejected (403)")
check(webhook({"type": "message.new", "account_id": "acc_123", "payload": {"id": "x"}}, ts=int(time.time()) - 3600).status_code == 403, "Stale timestamp rejected (403)")

before = len(client.get(f"/api/v1/inbox/conversations/{conv['id']}", headers=ADMIN).json()["messages"])
inbound("m_own", sender="919876500001@s.whatsapp.net", is_sender=True)
inbound("m_grp", sender="12345@g.us")
inbound("m_lid", sender="999@lid")  # no phone available anywhere -> ignored
after = len(client.get(f"/api/v1/inbox/conversations/{conv['id']}", headers=ADMIN).json()["messages"])
check(before == after and len(client.get("/api/v1/inbox/conversations", headers=ADMIN).json()) == len(convs),
      "Own messages, group chats and unresolvable @lid senders are ignored")

# @lid sender whose phone is exposed as public_identifier -> contact created, replies use the @lid chat id
webhook({"type": "message.new", "account_id": "acc_123", "payload": {
    "id": "m_lid_ok", "chat_id": "555111@lid", "sender_id": "555111@lid", "text": "hello from lid", "is_sender": False,
    "sender": {"display_name": "Lid Person", "public_identifier": "+91 98765 00099"}, "attachments": []}})
convs2 = client.get("/api/v1/inbox/conversations", headers=ADMIN).json()
lid_conv = next((c for c in convs2 if c["contact"]["phone"] == "+919876500099"), None)
check(lid_conv and lid_conv["contact"]["name"] == "Lid Person", "@lid sender is resolved via public_identifier")
r = client.post(f"/api/v1/inbox/conversations/{lid_conv['id']}/messages", headers=ADMIN, json={"type": "text", "text": "hi lid"})
check(fake.calls[-1]["url"] == "https://api.unipile.com/v2/acc_123/chats/555111@lid/messages/send", "Reply to an @lid contact uses the chat id exactly as Unipile returned it")

webhook({"type": "message.new", "account_id": "acc_123", "payload": {"id": "m_in_stop", "chat_id": "c", "sender_id": "919876500002@s.whatsapp.net", "text": "STOP", "is_sender": False, "attachments": []}})
contacts = client.get("/api/v1/contacts", headers=ADMIN).json()
items = contacts["items"] if isinstance(contacts, dict) else contacts
check(any(c["phone"] == "+919876500002" and c.get("opted_out") for c in items), "STOP opts the contact out")

# --- outbound -----------------------------------------------------------------
r = client.post(f"/api/v1/inbox/conversations/{conv['id']}/messages", headers=ADMIN, json={"type": "text", "text": "Yes! 20% off till Sunday"})
call = fake.calls[-1]
check(r.status_code == 200 and call["url"] == "https://api.unipile.com/v2/acc_123/chats/919876500001@s.whatsapp.net/messages/send",
      "Reply sent via POST /{account}/chats/{chat_id}/messages/send (chat_id = <digits>@s.whatsapp.net)")
check(call["json"] == {"text": "Yes! 20% off till Sunday"}, "Body is just the text")
check(call["headers"]["X-API-KEY"] == "key_test", "Send authenticated with X-API-KEY")
check(r.json()["status"] == "sent", "Reply stored as sent")

r = client.post(f"/api/v1/inbox/conversations/{conv['id']}/messages", headers=ADMIN, json={"type": "image", "media_url": "https://example.com/offer.png", "text": "Our offer"})
att = fake.calls[-1]["json"]["attachments"][0]
check(att["content_type"] == "image/png" and att["filename"] == "offer.png" and att["content"] and fake.calls[-1]["json"]["text"] == "Our offer",
      "Image downloaded and sent as base64 attachment with caption")

r = client.post(f"/api/v1/inbox/conversations/{conv['id']}/messages", headers=ADMIN, json={"type": "text", "content_sid": "HX1"})
check(r.json()["status"] == "failed" and "not supported" in (r.json().get("error_message") or ""), "Templates are reported as unsupported")

fake.new_chat = True
n_before = len(fake.calls)
r = client.post(f"/api/v1/inbox/conversations/{conv['id']}/messages", headers=ADMIN, json={"type": "text", "text": "hello again"})
check(r.json()["status"] == "sent" and len(fake.calls) == n_before + 2 and fake.calls[-1]["url"].endswith("/acc_123/chats/send")
      and fake.calls[-1]["json"]["users_ids"] == "919876500001@s.whatsapp.net", "404 (no chat yet) falls back to starting a chat")
fake.new_chat = False

fake.fail = True
r = client.post(f"/api/v1/inbox/conversations/{conv['id']}/messages", headers=ADMIN, json={"type": "text", "text": "x"})
check(r.json()["status"] == "failed" and "not on WhatsApp" in (r.json().get("error_message") or ""), "Provider errors are stored on the message")
fake.fail = False

# --- receipts + account status -------------------------------------------------
with SessionLocal() as db:
    reply = db.query(Message).filter(Message.text == "Yes! 20% off till Sunday").first()
    mid = reply.provider_message_id
check(bool(mid), "Unipile message id saved on the message")
webhook({"type": "message.receipt.read", "account_id": "acc_123", "payload": {"chat_id": "c", "message_id": mid, "user_id": "u", "timestamp": "t"}})
webhook({"type": "message.receipt.delivery", "account_id": "acc_123", "payload": {"chat_id": "c", "message_id": mid, "user_id": "u", "timestamp": "t"}})
with SessionLocal() as db:
    check(db.query(Message).filter(Message.provider_message_id == mid).first().status.value == "read", "Read receipt applied and never moved backwards")

webhook({"type": "account.status.disconnected", "account_id": "acc_123", "payload": {}})
with SessionLocal() as db:
    check(db.query(WhatsAppNumber).filter(WhatsAppNumber.unipile_account_id == "acc_123").first().status.value == "error", "account.status.disconnected marks the number as error")
webhook({"type": "account.status.running", "account_id": "acc_123", "payload": {}})
with SessionLocal() as db:
    check(db.query(WhatsAppNumber).filter(WhatsAppNumber.unipile_account_id == "acc_123").first().status.value == "active", "account.status.running restores it")

check(webhook({"type": "message.new", "account_id": "acc_unknown", "payload": {"id": "z"}}).status_code == 200, "Events for unknown accounts are acknowledged, not errors")



# --- change password -------------------------------------------------------------
H = lambda t: {"Authorization": f"Bearer {t}"}
tok = client.post("/api/v1/auth/login", json={"email": "boss@example.com", "password": "boot-pass-1"}).json()["access_token"]
check(client.post("/api/v1/auth/change-password", json={"current_password": "boot-pass-1", "new_password": "newpass-2222"}).status_code == 401, "Change password requires sign-in")
check(client.post("/api/v1/auth/change-password", headers=H(tok), json={"current_password": "wrong", "new_password": "newpass-2222"}).status_code == 400, "Wrong current password is rejected")
check(client.post("/api/v1/auth/change-password", headers=H(tok), json={"current_password": "boot-pass-1", "new_password": "short"}).status_code == 422, "Too-short new password is rejected (min 8)")
check(client.post("/api/v1/auth/change-password", headers=H(tok), json={"current_password": "boot-pass-1", "new_password": "boot-pass-1"}).status_code == 400, "New password must differ from the current one")
check(client.post("/api/v1/auth/change-password", headers=H(tok), json={"current_password": "boot-pass-1", "new_password": "newpass-2222"}).status_code == 204, "Password changed")
check(client.post("/api/v1/auth/login", json={"email": "boss@example.com", "password": "boot-pass-1"}).status_code == 401
      and client.post("/api/v1/auth/login", json={"email": "boss@example.com", "password": "newpass-2222"}).status_code == 200, "Old password stops working, new one works")

# --- live connection status ------------------------------------------------------
import app.services.connection_status as cs  # noqa: E402
account_status = {"acc_123": {"id": "acc_123", "status": "running", "is_locked": False}, "acc_456": {"id": "acc_456", "status": "running", "is_locked": False}}
_prev_get = httpx.get
def fake_account_get(url, **kw):
    aid = url.rsplit("/", 1)[-1]
    return httpx.Response(200, json=account_status[aid]) if aid in account_status else httpx.Response(404, json={})
httpx.get = fake_account_get
cs._cache.clear()
live = client.get("/api/v1/numbers/live-status", headers=SA).json()
mine = next(n for n in live if n["phone"] == "+919876543210")
check(mine["connection"] == "connected" and mine["checked_at"], "Live status: running account shows connected")
check(any(n["connection"] == "not_linked" for n in live), "Numbers without a linked account show not_linked")
account_status["acc_123"]["status"] = "disconnected"; cs._cache.clear()
mine = next(n for n in client.get("/api/v1/numbers/live-status", headers=SA).json() if n["phone"] == "+919876543210")
check(mine["connection"] == "disconnected", "Live status reflects a disconnect right away")
check(next(n for n in client.get("/api/v1/numbers", headers=SA).json() if n["phone"] == "+919876543210")["status"] == "error", "Stored number status follows the live check")
account_status["acc_123"]["status"] = "degraded"; cs._cache.clear()
check(next(n for n in client.get("/api/v1/numbers/live-status", headers=SA).json() if n["phone"] == "+919876543210")["connection"] == "attention", "Degraded shows needs-attention")
httpx.get = lambda url, **kw: (_ for _ in ()).throw(httpx.ConnectError("down")); cs._cache.clear()
check(next(n for n in client.get("/api/v1/numbers/live-status", headers=SA).json() if n["phone"] == "+919876543210")["connection"] == "unknown", "Provider unreachable shows unknown (not a false disconnect)")
httpx.get = _prev_get
account_status["acc_123"]["status"] = "running"; cs._cache.clear()
httpx.get = fake_account_get
ADMIN_LIVE = client.get("/api/v1/numbers/live-status", headers=ADMIN).json()
check(ADMIN_LIVE and all(n["admin_id"] == rahul_id for n in ADMIN_LIVE) and len(ADMIN_LIVE) < len(client.get("/api/v1/numbers/live-status", headers=SA).json()), "An Admin only sees live status of their own numbers")
httpx.get = _prev_get

# --- neutral surface: legacy path still works, provider name never reaches the screen
check(webhook({"type": "message.new", "account_id": "acc_unknown", "payload": {"id": "z"}}, path="/api/v1/unipile/webhook").status_code == 200, "Legacy webhook path still works (already-registered endpoints)")
client.post("/api/v1/inbox/conversations/" + conv["id"] + "/messages", headers=ADMIN, json={"type": "text", "content_sid": "HX1"})
logs = json.dumps(client.get("/api/v1/activity-logs", headers=SA).json())
with SessionLocal() as db:
    errors = " ".join(m.error_message or "" for m in db.query(Message).all())
check("unipile" not in (logs + errors).lower(), "Activity logs and message errors never mention the provider")
paths = " ".join(client.get("/openapi.json").json()["paths"])
check("unipile" not in paths.lower(), "Public API docs list neutral paths only")
check("unipile" not in json.dumps(client.get("/api/v1/numbers", headers=SA).json()).lower(), "Numbers API responses never mention the provider")

# --- automations: new_customer trigger -------------------------------------------
import app.services.automation_runner as ar  # noqa: E402
from datetime import timedelta  # noqa: E402
from app.models.automation import Automation, AutomationRun, AutomationStatus  # noqa: E402
from app.models.conversation import Conversation  # noqa: E402

with SessionLocal() as db:  # silence the seeded demo automations
    for a in db.query(Automation).all():
        a.status = AutomationStatus.PAUSED
    db.query(AutomationRun).delete()  # runs left by seeded demo automations during earlier checks
    db.commit()

flow = {"nodes": [
    {"type": "trigger", "label": "New customer"},
    {"type": "action", "label": "Send welcome message", "messageVariants": ["Hi {{name}}, welcome to Relay!"]},
    {"type": "delay", "label": "Wait 2 hours"},
    {"type": "condition", "label": "Customer replied?"},
    {"type": "action", "label": "Transfer to human", "sub": "YES"},
    {"type": "action", "label": "Send follow-up", "sub": "NO", "messageVariants": ["Still interested, {{name}}?"]},
]}
auto = client.post("/api/v1/automations", headers=ADMIN, json={"name": "Welcome flow", "trigger_type": "new_customer", "flow": flow}).json()
check(ar.parse_delay_minutes({"label": "Wait 2 hours"}) == 120 and ar.parse_delay_minutes({"label": "30 min"}) == 30
      and ar.parse_delay_minutes({"label": "1 day"}) == 1440 and ar.parse_delay_minutes({"label": "?"}) == 60
      and ar.parse_delay_minutes({"delay_minutes": 5, "label": "Wait 2 hours"}) == 5, "Delay parsing (label, delay_minutes, default)")

inbound("a_new_1", sender="919800000001@s.whatsapp.net", text="hello")  # automation not running yet
check(ar.process_due_runs() == 0, "A Scheduled/Paused automation does not trigger")

client.patch(f"/api/v1/automations/{auto['id']}", headers=ADMIN, json={"status": "Running"})
n0 = len(fake.calls)
inbound("a_new_2", sender="919800000002@s.whatsapp.net", text="hi there")
check(len(fake.calls) == n0, "Webhook only queues the run (fast); nothing sent inside the request")
check(ar.process_due_runs() == 1, "Poller picks up the new run")
c = fake.calls[-1]
check(c["url"].endswith("/chats/919800000002@s.whatsapp.net/messages/send") and c["json"]["text"] == "Hi Asha, welcome to Relay!",
      "Welcome message sent to the new customer, {{name}} personalised")
check(ar.process_due_runs() == 0, "Run now waits (delay) — nothing due yet")

inbound("a_old", sender="919800000002@s.whatsapp.net", text="a second message")
with SessionLocal() as db:
    check(db.query(AutomationRun).count() == 1, "An existing contact does not start another run")

def fast_forward():
    with SessionLocal() as db:
        for r in db.query(AutomationRun).filter(AutomationRun.status == "pending").all():
            r.run_at = utcnow_() - timedelta(seconds=1)
        db.commit()

from app.services.messaging import utcnow as utcnow_  # noqa: E402
n1 = len(fake.calls)
fast_forward()
ar.process_due_runs()
# customer 2 DID reply after the trigger (message "a_old") -> YES branch: hand over to a human, no follow-up text
with SessionLocal() as db:
    conv2 = db.query(Conversation).filter(Conversation.provider_chat_id == "919800000002@s.whatsapp.net").first()
    check(conv2.status.value == "needs_human", "Customer replied -> YES branch hands the chat to a human")
check(len(fake.calls) == n1, "Replied customer gets no follow-up message")

# customer 3 never replies -> NO branch: follow-up sent after the wait
inbound("a_new_3", sender="919800000003@s.whatsapp.net", text="hey")
ar.process_due_runs()
n2 = len(fake.calls)
fast_forward()
ar.process_due_runs()
check(len(fake.calls) == n2 + 1 and fake.calls[-1]["json"]["text"].startswith("Still interested"), "Silent customer -> NO branch sends the follow-up")
with SessionLocal() as db:
    a = db.get(Automation, uuid.UUID(auto["id"]))
    check(a.recipients == 2 and a.sent == 3 and a.replies == 1, "Automation counters updated (recipients / sent / replies)")
    check(db.query(AutomationRun).filter(AutomationRun.status == "pending").count() == 0, "All runs finished")

# opted-out first message: STOP as a brand-new contact -> no automation
n3 = len(fake.calls)
inbound("a_stop", sender="919800000004@s.whatsapp.net", text="STOP")
ar.process_due_runs()
check(len(fake.calls) == n3, "A new contact whose first message is STOP is not messaged")

# paused automation stops in-flight runs
inbound("a_new_5", sender="919800000005@s.whatsapp.net", text="hi")
ar.process_due_runs()
client.patch(f"/api/v1/automations/{auto['id']}", headers=ADMIN, json={"status": "Paused"})
n4 = len(fake.calls)
fast_forward()
ar.process_due_runs()
check(len(fake.calls) == n4, "Pausing the automation stops runs that are waiting")

httpx.get = httpx_get
print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
