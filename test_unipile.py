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
    AUTOMATION_REPLY_DELAY_MIN="0", AUTOMATION_REPLY_DELAY_MAX="0",
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
from app.models.conversation import Message, MessageSender, MessageStatus, MessageType  # noqa: E402
from app.models.whatsapp_number import WhatsAppNumber  # noqa: E402


class FakeHttp:
    """Stands in for httpx.Client in the sending service."""

    def __init__(self):
        self.calls = []
        self.fail = False
        self.new_chat = False
        self.fixed_id = None
        self.user_chats = {}   # jid -> existing chat id, for GET /{account}/users/{jid}/chat
        self.gets = []

    def post(self, url, json=None, headers=None):
        self.calls.append({"url": url, "json": json, "headers": headers})
        if self.new_chat and "/messages/send" in url:
            return httpx.Response(404, json={"detail": "chat not found"})
        if self.fail:
            return httpx.Response(422, json={"detail": "Recipient is not on WhatsApp"})
        return httpx.Response(200, json={"object": "ChatStarted", "chat_id": "c1", "message_id": self.fixed_id or f"msg_{len(self.calls)}"})

    def get(self, url, headers=None, follow_redirects=True):
        if "/users/" in url and url.endswith("/chat"):
            self.gets.append(url)
            jid = url.split("/users/")[1][: -len("/chat")]
            if jid in self.user_chats:
                return httpx.Response(200, json={"object": "Chat", "id": self.user_chats[jid]}, request=httpx.Request("GET", url))
            return httpx.Response(404, json={"detail": "no chat"}, request=httpx.Request("GET", url))
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
        {"id": "0@s.whatsapp.net", "name": "WhatsApp Business", "user_id": "0@s.whatsapp.net", "unread_count": 0},
        {"id": "120363111@g.us", "name": "Family group", "user_id": None, "unread_count": 5},
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
    "0@s.whatsapp.net": [_m("sys1", "Welcome to WhatsApp Business", "2026-11-08T01:18:00.000Z")],
    "120363111@g.us": [_m("grp1", "group chatter", "2026-11-10T06:00:00.000Z")],
    "919822222222@s.whatsapp.net": [_m("s1", "Hello second number", "2026-11-10T11:00:00.000Z")],
}
USERS = {"555777@lid": "+91 98111 00099"}


PROVIDER = {"all_fail": None, "list_fail": None, "body": {"detail": "nope"}}
KEYS_USED = []
VISIBLE_FOR_KEY = {}   # key -> account ids that key can see (a Scoped key sees one scope only)
ENDPOINTS = []
DELIVERIES = []


def provider_get(url, params=None, **kw):
    path = url.split("/v2", 1)[1]
    req = httpx.Request("GET", url)
    R = lambda code, data: httpx.Response(code, json=data, request=req)
    KEYS_USED.append((path, (kw.get("headers") or {}).get("X-API-KEY")))
    if PROVIDER["all_fail"]:
        return R(PROVIDER["all_fail"], PROVIDER["body"])
    if path.startswith("/webhooks/endpoints"):
        return R(200, {"object": "WebhookEndpoints", "data": ENDPOINTS, "has_more": False})
    if path.startswith("/webhooks/conversations"):
        return R(200, {"object": "WebhookConversations", "data": DELIVERIES, "has_more": False})
    if path == "/accounts" and PROVIDER["list_fail"]:
        return R(PROVIDER["list_fail"], {"detail": "not allowed"})
    if path == "/accounts":
        key = (kw.get("headers") or {}).get("X-API-KEY")
        shown = [a for a in ACCOUNTS if a["id"] in VISIBLE_FOR_KEY[key]] if key in VISIBLE_FOR_KEY else ACCOUNTS
        return R(200, {"object": "Accounts", "data": shown, "has_more": False})
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


def check(cond, label, extra=""):
    global passed, failed
    passed, failed = passed + bool(cond), failed + (not cond)
    print(("[OK] " if cond else "[FAIL] ") + label + (f"   -> {extra}" if (extra and not cond) else ""))


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
from app.core.config import settings  # noqa: E402
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
nop = next((c for c in convs if c["contact"]["name"] == "No Phone"), None)
check(nop and nop["contact"]["phone"] == "lid:nophone", "A chat whose phone WhatsApp hides is still imported, keyed by its own chat id")
check(not any(c["contact"]["name"] in ("WhatsApp Business", "Family group") or c["contact"]["phone"] in ("+0", "lid:0") for c in convs),
      "WhatsApp's own system chat ('+0') and groups are not imported as customers")
check({byphone["+919811111111"]["whatsapp_number_phone"], byphone["+919822222222"]["whatsapp_number_phone"]} == {"+919876543210", "+919123456789"}
      and byphone["+919822222222"]["whatsapp_number_name"], "Each conversation says which number it belongs to")
only = client.get("/api/v1/inbox/conversations", headers=SA, params={"number_id": num2["id"]}).json()
check(only and all(c["whatsapp_number_id"] == num2["id"] for c in only) and len(only) < len(convs), "Filtering by number shows only that number's chats")
stats = next(n for n in client.get("/api/v1/numbers", headers=SA).json() if n["id"] == num["id"])
check(stats["chats"] == 3 and stats["unread"] == 2, "Numbers list shows chats + unread per number", f"{stats['chats']} chats, {stats['unread']} unread")
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

# --- assigning a number moves its whole inbox ------------------------------------
admins_list = client.get("/api/v1/admins", headers=SA).json()
other = next(a for a in admins_list if a["id"] != rahul_id)
other_h = {"Authorization": "Bearer " + client.post("/api/v1/auth/login", json={"email": other["email"], "password": "password123"}).json()["access_token"]}
def phones(h):
    return {c["contact"]["phone"] for c in client.get("/api/v1/inbox/conversations", headers=h).json()}
check("+919822222222" in phones(ADMIN) and "+919822222222" not in phones(other_h), "Before: only the current owner sees the second number's chats")
check(client.patch(f"/api/v1/numbers/{num2['id']}", headers=ADMIN, json={"admin_id": other["id"]}).status_code == 403, "An Admin cannot reassign a number")
check(client.patch(f"/api/v1/numbers/{num2['id']}", headers=SA, json={"admin_id": str(uuid.uuid4())}).status_code == 404, "Assigning to an unknown admin is rejected")
r = client.patch(f"/api/v1/numbers/{num2['id']}", headers=SA, json={"admin_id": other["id"]})
check(r.status_code == 200 and r.json()["admin_id"] == other["id"] and r.json()["admin_name"] == other["name"], "Super Admin assigns the number to another Admin")
check("+919822222222" in phones(other_h) and "+919822222222" not in phones(ADMIN), "The chats moved with the number")
check(any(c["phone"] == "+919822222222" for c in (lambda x: x["items"] if isinstance(x, dict) else x)(client.get("/api/v1/contacts", headers=other_h).json())), "...and so did its contacts")
check(next(n for n in client.get("/api/v1/numbers", headers=SA).json() if n["id"] == num2["id"])["admin_name"] == other["name"], "Numbers list shows who each number is assigned to")
check("assigned" in json.dumps(client.get("/api/v1/activity-logs", headers=SA).json()), "Assignment is recorded in the activity log")
client.patch(f"/api/v1/numbers/{num2['id']}", headers=SA, json={"admin_id": rahul_id})
check("+919822222222" in phones(ADMIN), "Assigning it back restores the owner's inbox")

# --- demo-password warning ------------------------------------------------------
import logging  # noqa: E402
from app.bootstrap import warn_default_passwords  # noqa: E402
records = []
class _H(logging.Handler):
    def emit(self, rec): records.append(rec.getMessage())
h = _H(); logging.getLogger("relay.bootstrap").addHandler(h)
warn_default_passwords()
logging.getLogger("relay.bootstrap").removeHandler(h)
check(any("demo password" in m and "deepa@relaycrm.io" in m for m in records), "Startup warns about accounts that still use the demo password")

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
n_before = len(client.get("/api/v1/inbox/conversations", headers=ADMIN).json())
inbound("m_grp", sender="12345@g.us")
inbound("m_sys", sender="0@s.whatsapp.net")
after = len(client.get(f"/api/v1/inbox/conversations/{conv['id']}", headers=ADMIN).json()["messages"])
check(before == after and len(client.get("/api/v1/inbox/conversations", headers=ADMIN).json()) == n_before,
      "Group chats and WhatsApp's own system chat are ignored")
unread_before = next(c for c in client.get("/api/v1/inbox/conversations", headers=ADMIN).json() if c["id"] == conv["id"])["unread_count"]
inbound("m_own", "Replying from my phone", sender="919876500001@s.whatsapp.net", is_sender=True)
d_own = client.get(f"/api/v1/inbox/conversations/{conv['id']}", headers=ADMIN).json()
mine = [m for m in d_own["messages"] if m["text"] == "Replying from my phone"]
check(len(mine) == 1 and mine[0]["sender"] == "business" and mine[0]["status"] == "sent", "A message sent from the phone (message.new with is_sender) shows in the thread as ours, live")
inbound("m_own", "Replying from my phone", sender="919876500001@s.whatsapp.net", is_sender=True)
check(len([m for m in client.get(f"/api/v1/inbox/conversations/{conv['id']}", headers=ADMIN).json()["messages"] if m["text"] == "Replying from my phone"]) == 1, "...and a repeated notification does not add it twice")
inbound("m_lid", "hidden number here", sender="999@lid")  # WhatsApp shares no phone at all
lid999 = next((c for c in client.get("/api/v1/inbox/conversations", headers=ADMIN).json() if c["contact"]["phone"] == "lid:999"), None)
check(lid999 is not None and lid999["unread_count"] == 1, "A sender WhatsApp identifies only by a hidden id is not lost: the chat is created")
client.post(f"/api/v1/inbox/conversations/{lid999['id']}/messages", headers=ADMIN, json={"type": "text", "text": "got it"})
check(fake.calls[-1]["url"] == "https://api.unipile.com/v2/acc_123/chats/999@lid/messages/send", "...and you can answer it: the reply goes to that exact chat id")

# @lid sender whose phone is exposed as public_identifier -> contact created, replies use the @lid chat id
webhook({"type": "message.new", "account_id": "acc_123", "payload": {
    "id": "m_lid_ok", "chat_id": "555111@lid", "sender_id": "555111@lid", "text": "hello from lid", "is_sender": False,
    "sender": {"display_name": "Lid Person", "public_identifier": "+91 98765 00099"}, "attachments": []}})
convs2 = client.get("/api/v1/inbox/conversations", headers=ADMIN).json()
lid_conv = next((c for c in convs2 if c["contact"]["phone"] == "+919876500099"), None)
check(lid_conv and lid_conv["contact"]["name"] == "Lid Person", "@lid sender is resolved via public_identifier")
r = client.post(f"/api/v1/inbox/conversations/{lid_conv['id']}/messages", headers=ADMIN, json={"type": "text", "text": "hi lid"})
check(fake.calls[-1]["url"] == "https://api.unipile.com/v2/acc_123/chats/555111@lid/messages/send", "Reply to an @lid contact uses the chat id exactly as Unipile returned it")

webhook({"type": "message.new", "account_id": "acc_123", "payload": {"id": "m_in_stop", "chat_id": "919876500002@s.whatsapp.net", "sender_id": "919876500002@s.whatsapp.net", "text": "STOP", "is_sender": False, "attachments": []}})
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



# --- real figures: every number is counted from the messages ----------------------
from datetime import datetime as _dt, timezone as _tz  # noqa: E402
from app.models.conversation import Conversation, MessageType  # noqa: E402
with SessionLocal() as db:
    rows = db.query(Message, Conversation.admin_id, Conversation.whatsapp_number_id, Conversation.id).join(
        Conversation, Conversation.id == Message.conversation_id).filter(Message.type != MessageType.NOTE).all()
today = _dt.now(_tz.utc).date()
def _day(m):
    d = m.created_at
    return (d.replace(tzinfo=_tz.utc) if d.tzinfo is None else d).astimezone(_tz.utc).date()
def expected(pred):
    out = [r for r in rows if pred(r) and r[0].sender.value in ("business", "bot")]
    st = lambda *names: sum(r[0].status.value in names for r in out)
    return {"sent": st("sent", "delivered", "read"), "delivered": st("delivered", "read"), "read": st("read"),
            "failed": st("failed", "undelivered"), "received": sum(r[0].sender.value == "customer" for r in rows if pred(r))}
allx = expected(lambda r: True)
tot = client.get("/api/v1/analytics/summary", headers=SA).json()
check((tot["messages_sent"], tot["delivered"], tot["read"], tot["failed"], tot["replies"]) ==
      (allx["sent"], allx["delivered"], allx["read"], allx["failed"], allx["received"]), "Analytics totals equal the real message counts")
check(allx["sent"] > 0 and allx["read"] > 0 and allx["failed"] > 0 and allx["received"] > 0 and tot["read"] != round(tot["delivered"] * 0.65), "...and 'read' is counted, not estimated from delivered")
rx = expected(lambda r: str(r[1]) == rahul_id)
mine = client.get("/api/v1/analytics/summary", headers=ADMIN).json()
check((mine["messages_sent"], mine["delivered"], mine["read"], mine["failed"], mine["replies"]) == (rx["sent"], rx["delivered"], rx["read"], rx["failed"], rx["received"]),
      "An Admin's analytics only count their own chats")
ts = client.get("/api/v1/analytics/timeseries", headers=SA).json()
check(len(ts) == 14 and ts[-1]["date"] == today.isoformat() and [t["date"] for t in ts] == sorted(t["date"] for t in ts), "Time series: last 14 days, ending today")
sent_today = sum(1 for r in rows if r[0].sender.value in ("business", "bot") and r[0].status.value in ("sent", "delivered", "read") and _day(r[0]) == today)
check(ts[-1]["sent"] == sent_today and sent_today > 0, "Today's point equals the messages really sent today")
check(sum(t["received"] for t in ts) == sum(1 for r in rows if r[0].sender.value == "customer" and (today - _day(r[0])).days < 14), "Received per day adds up to the real inbound messages")
check(client.get("/api/v1/analytics/timeseries", headers=SA, params={"days": 500}).status_code == 422, "Time series range is bounded")
nlist = client.get("/api/v1/numbers", headers=SA).json()
n1 = next(n for n in nlist if n["id"] == num["id"])
x1 = [r for r in rows if str(r[2]) == num["id"] and r[0].sender.value in ("business", "bot")]
ok1 = [r for r in x1 if r[0].status.value in ("sent", "delivered", "read")]
dlv = [r for r in ok1 if r[0].status.value in ("delivered", "read")]
wrote = {r[3] for r in ok1}
answered = {r[3] for r in rows if str(r[2]) == num["id"] and r[0].sender.value == "customer"}
check(n1["messages_today"] == sum(_day(r[0]) == today for r in ok1) and n1["messages_today"] > 0, "A number's 'messages today' is counted from its messages")
check(n1["delivery_rate"] == round(len(dlv) / len(ok1) * 100, 1) and n1["reply_rate"] == round(len(wrote & answered) / len(wrote) * 100, 1), "Delivery and reply rates are computed, not stored")
adm = next(a for a in client.get("/api/v1/admins", headers=SA).json() if a["id"] == rahul_id)
check(adm["messages_sent"] == rx["sent"] and adm["replies"] == rx["received"], "Admin performance table uses the real counts")

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

# --- ready-made automation templates ---------------------------------------------
tl = client.get("/api/v1/automations/templates", headers=ADMIN).json()
check(client.get("/api/v1/automations/templates").status_code == 401, "The template gallery needs a sign-in")
check({t["id"] for t in tl} == {"welcome", "welcome_followup", "handoff", "qualify", "keyword_reply", "keyword_human"} and all(t["steps"] and t["description"] and t["starts_when"] for t in tl), "An Admin gets the template gallery")
check(client.post("/api/v1/automations/from-template", headers=ADMIN, json={"template_id": "nope"}).status_code == 404, "An unknown template is rejected")
_make = lambda tid, h=ADMIN, **kw: client.post("/api/v1/automations/from-template", headers=h, json={"template_id": tid, **kw})
r = _make("welcome", name="My welcome")
check(r.status_code == 200 and r.json()["name"] == "My welcome" and r.json()["status"] == "Scheduled" and r.json()["admin_id"] == rahul_id and r.json()["trigger_type"] == "new_customer",
      "Creating from a template makes a Scheduled automation owned by the Admin")
check(_make("welcome").json()["name"] == "Welcome new customers" and "My welcome" in [a["name"] for a in client.get("/api/v1/automations", headers=ADMIN).json()], "Default name is the template's; it shows in the Admin's list")
check(_make("handoff", h=SA).status_code == 200, "A Super Admin can use templates too")
check("from the" in json.dumps(client.get("/api/v1/activity-logs", headers=SA).json()), "Creating from a template is logged")

def only_running(aid):
    with SessionLocal() as db:
        for a in db.query(Automation).all():
            a.status = AutomationStatus.PAUSED
        db.query(AutomationRun).delete()
        if aid:
            db.get(Automation, uuid.UUID(aid)).status = AutomationStatus.RUNNING
        db.commit()
_n = iter(range(100, 999))
def new_customer(text="hello"):
    sender = f"91985{next(_n):07d}@s.whatsapp.net"
    inbound("tpl_" + sender[:12], text, sender)
    ar.process_due_runs()
    return sender
def notes(sender):
    with SessionLocal() as db:
        conv = db.query(Conversation).filter(Conversation.provider_chat_id == sender).first()
        return [m.text for m in db.query(Message).filter(Message.conversation_id == conv.id).all() if m.type.value == "note"]

# welcome
only_running(_make("welcome").json()["id"]); n0 = len(fake.calls); c = new_customer()
check(len(fake.calls) == n0 + 1 and fake.calls[-1]["json"]["text"] == "Hi Asha! Thanks for contacting us. How can we help you today?", "Template 'Welcome': greets a new customer, name filled in")
# welcome + follow-up: silent customer
only_running(_make("welcome_followup").json()["id"]); n0 = len(fake.calls); silent = new_customer()
check(len(fake.calls) == n0 + 1 and "How can we help" in fake.calls[-1]["json"]["text"], "Template 'Welcome + follow-up': welcome goes out first")
fast_forward(); ar.process_due_runs()
check(len(fake.calls) == n0 + 2 and "just checking in" in fake.calls[-1]["json"]["text"], "...a silent customer gets the follow-up after the wait")
# welcome + follow-up: customer replies
only_running(_make("welcome_followup").json()["id"]); talker = new_customer()
inbound("tpl_reply", "yes please", talker)
n0 = len(fake.calls); fast_forward(); ar.process_due_runs()
check(len(fake.calls) == n0 and any("handed this chat to a human" in (t or "") for t in notes(talker)), "...a customer who replied is handed to a human, no follow-up")
# hand over
only_running(_make("handoff").json()["id"]); n0 = len(fake.calls); h = new_customer()
check(len(fake.calls) == n0 + 1 and fake.calls[-1]["json"]["text"].startswith("Thanks Asha, a member of our team") and any("handed this chat to a human" in (t or "") for t in notes(h)),
      "Template 'Hand over to a human': acknowledges, then flags the chat")
# qualify
only_running(_make("qualify").json()["id"]); n0 = len(fake.calls); q = new_customer()
check(len(fake.calls) == n0 + 1 and "budget" in fake.calls[-1]["json"]["text"], "Template 'Qualify new leads': asks what they need")
fast_forward(); ar.process_due_runs()
check(len(fake.calls) == n0 + 2 and "whenever you are ready" in fake.calls[-1]["json"]["text"], "...and reminds a silent lead after a day")
# message variants: rotation and random
two = {"nodes": [{"id": "trigger", "type": "trigger", "label": "New customer"},
                 {"id": "w", "type": "action", "label": "Send message", "messageVariants": ["Variant A for {{name}}", "Variant B for {{name}}"], "assignmentMode": "round_robin"}]}
aid = client.post("/api/v1/automations", headers=ADMIN, json={"name": "Two variants", "flow": two}).json()["id"]
only_running(aid); first = (new_customer(), fake.calls[-1]["json"]["text"])[1]; second = (new_customer(), fake.calls[-1]["json"]["text"])[1]
check([first, second] == ["Variant A for Asha", "Variant B for Asha"], "Round-robin rotates through the message variants")
two["nodes"][1]["assignmentMode"] = "random"
client.patch(f"/api/v1/automations/{aid}", headers=ADMIN, json={"flow": two}); only_running(aid)
seen = {(new_customer(), fake.calls[-1]["json"]["text"])[1] for _ in range(12)}
check(seen <= {"Variant A for Asha", "Variant B for Asha"} and len(seen) == 2, "Random picks among the variants")
only_running(None)

# --- the built-in (hard-coded) flow runs without anyone writing a message -----------
builder_default = {"nodes": [  # exactly what the builder shows for a new automation
    {"id": "trigger", "type": "trigger", "label": "New customer", "sub": "Trigger", "icon": "Zap"},
    {"id": "welcome", "type": "action", "label": "Send welcome message", "sub": "Text + image", "icon": "Send"},
    {"id": "delay", "type": "delay", "label": "Wait 2 hours", "sub": "Delay", "icon": "Clock"},
    {"id": "condition", "type": "condition", "label": "Customer replied?", "sub": "Condition", "icon": "GitBranch"},
    {"id": "yes", "type": "action", "label": "Transfer to human", "sub": "YES", "icon": "UserCog"},
    {"id": "no", "type": "action", "label": "Send follow-up", "sub": "NO", "icon": "Send"},
]}
aid = client.post("/api/v1/automations", headers=ADMIN, json={"name": "Builder default", "flow": builder_default}).json()["id"]
only_running(aid); n0 = len(fake.calls); c1 = new_customer()
check(len(fake.calls) == n0 + 1 and fake.calls[-1]["json"]["text"] == "Hi Asha! Thanks for contacting us. How can we help you today?", "The builder's default flow: welcome goes out with built-in wording")
fast_forward(); ar.process_due_runs()
check(len(fake.calls) == n0 + 2 and "just checking in" in fake.calls[-1]["json"]["text"], "...then, after 2 hours of silence, the follow-up")
only_running(aid); c2 = new_customer(); inbound("dflt_reply", "hi again", c2); n0 = len(fake.calls); fast_forward(); ar.process_due_runs()
check(len(fake.calls) == n0 and any("handed this chat to a human" in (t or "") for t in notes(c2)), "...and a customer who answered is transferred to a human")
# an automation that has no saved steps at all runs the same flow
blank = client.post("/api/v1/automations", headers=ADMIN, json={"name": "Nothing saved yet"}).json()
only_running(blank["id"]); n0 = len(fake.calls); new_customer()
check(blank["flow"] == {} and len(fake.calls) == n0 + 1 and "Thanks for contacting us" in fake.calls[-1]["json"]["text"], "An automation with no saved steps runs the built-in flow")
# older shape (the demo data): trigger + one welcome step without any text
old = client.post("/api/v1/automations", headers=ADMIN, json={"name": "Old shape", "flow": {"nodes": [
    {"type": "trigger", "label": "New customer"}, {"type": "action", "label": "Send welcome message"}]}}).json()
only_running(old["id"]); n0 = len(fake.calls); new_customer()
check(len(fake.calls) == n0 + 1 and "Thanks for contacting us" in fake.calls[-1]["json"]["text"], "A welcome step with no text still sends the built-in welcome")
only_running(None)

# --- importer: real phone, visible errors, fallback ids, catch-up ------------------------
from app.core.config import Settings  # noqa: E402
check(Settings(_env_file=None, WHATSAPP_MOCK=True, UNIPILE_API_KEY="k").WHATSAPP_MOCK is False and Settings(_env_file=None, WHATSAPP_MOCK=True, UNIPILE_API_KEY="").WHATSAPP_MOCK is True,
      "A configured API key always means live mode, even if WHATSAPP_MOCK=true is still set")
ACCOUNTS.append({"id": "acc_789", "name": "Shop Line", "user_id": "919800000789@s.whatsapp.net", "status": "running", "provider": "whatsapp"})
with SessionLocal() as db:
    created = sync.sync_accounts(db)
shop = next(n for n in client.get("/api/v1/numbers", headers=SA).json() if n["display_name"] == "Shop Line")
check(len(created) == 1 and shop["phone"] == "+919800000789", "The number's phone comes from the account's own id, the name stays as the label")

ACCOUNTS[0]["application_id"] = "app_01test"
_saved = list(ACCOUNTS); ACCOUNTS.clear()
rep = sync.refresh_now()
check(rep["ok"] is False and "no WhatsApp accounts" in rep["error"] and "application" in rep["error"], "A key that sees zero accounts is explained (wrong application?)")
ACCOUNTS.extend(_saved)
check(sync.refresh_now()["ok"] is True and sync.STATE["application_id"] == "app_01test", "The application the key belongs to is reported (compare it with your dashboard)")
for _ in range(60):
    if not sync.STATE["history_importing"]:
        break
    __import__("time").sleep(0.1)
PROVIDER["all_fail"] = 401
rep = sync.refresh_now()
check(rep["ok"] is False and "401" in rep["error"] and "API key" in rep["error"], "A rejected key is reported in plain words")
check("401" in client.get("/api/v1/system/whatsapp", headers=SA).json()["sync"]["error"] and client.get("/api/v1/system/whatsapp", headers=ADMIN).json()["sync"]["error"] is None,
      "The Super Admin sees the sync error in the status; Admins don't")
PROVIDER["all_fail"] = None
PROVIDER["list_fail"] = 403
rep = sync.refresh_now()
check(rep["ok"] is False and "403" in rep["error"] and "Service Key" in rep["error"] and "UNIPILE" not in rep["error"], "A key that cannot list accounts says what to do (without naming settings)")
settings.UNIPILE_ACCOUNT_IDS = "acc_123, acc_456, acc_789"
rep = sync.refresh_now()
check(rep["ok"] is True and rep["accounts_found"] == 3, "...and listing the account ids by hand is enough")
settings.UNIPILE_ACCOUNT_IDS = ""
PROVIDER["list_fail"] = None
rep = sync.refresh_now()
check(rep["ok"] is True and rep["accounts_found"] == 3 and sync.STATE["error"] is None, "Once fixed, a refresh succeeds and clears the error")
import time as _t  # noqa: E402
for _ in range(60):
    if not sync.STATE["history_importing"]:
        break
    _t.sleep(0.1)

# catch-up: a new customer message + a message we sent from this app (stored under another id)
sent = client.post(f"/api/v1/inbox/conversations/{byphone['+919811111111']['id']}/messages", headers=SA, json={"type": "text", "text": "Thanks, noted!"}).json()
from datetime import timedelta as _td2  # noqa: E402
now_iso = (_dt.now(_tz.utc) + _td2(seconds=30)).strftime("%Y-%m-%dT%H:%M:%S.000Z")  # after the reply we just sent
MESSAGES["919811111111@s.whatsapp.net"].insert(0, _m("w5", "One more question", now_iso))
MESSAGES["919811111111@s.whatsapp.net"].insert(0, _m("provider_id_for_sent", "Thanks, noted!", now_iso, True))
CHATS["acc_123"][0]["last_message_timestamp"] = now_iso
with SessionLocal() as db:
    nm = db.query(WhatsAppNumber).filter(WhatsAppNumber.unipile_account_id == "acc_123").first()
    before_msgs = db.query(Message).count()
    changed = sync.sync_recent(db, nm)
    texts = [m.text for m in db.query(Message).join(Conversation, Conversation.id == Message.conversation_id).filter(Conversation.provider_chat_id == "919811111111@s.whatsapp.net").all()]
    adopted = db.query(Message).filter(Message.provider_message_id == "provider_id_for_sent").count()
    check(changed >= 1 and "One more question" in texts, "Catch-up sync picks up a customer message that no webhook delivered")
    check(texts.count("Thanks, noted!") == 1 and adopted == 1, "A message sent from this app is not shown twice: it adopts the service's id")
    check(db.query(Message).count() == before_msgs + 1, "Only the genuinely new message was added")
    check(sync.sync_recent(db, nm) == 0, "Nothing new: the next catch-up reads nothing")
    conv_row = db.query(Conversation).filter(Conversation.provider_chat_id == "919811111111@s.whatsapp.net").first()
    check(conv_row.unread_count >= 1, "The new message counts as unread")

# --- attachments and voice notes ---------------------------------------------------
import base64 as _b64  # noqa: E402
import subprocess as _sp  # noqa: E402
import app.api.v1.endpoints.inbox as _inbox  # noqa: E402
from app.services.media import sign as _sign  # noqa: E402
from app.services.audio import ffmpeg_path as _ffmpeg  # noqa: E402
att_conv = byphone["+919811111111"]["id"]
PNG = b"\x89PNG\r\n\x1a\n" + bytes(range(200))
def upload(name, data, ctype, h=SA, cid=None, **form):
    return client.post(f"/api/v1/inbox/conversations/{cid or att_conv}/attachments", headers=h, files={"file": (name, data, ctype)}, data=form)

r = upload("photo.png", PNG, "image/png", caption="Look at this")
sent_body = fake.calls[-1]["json"]; att = sent_body["attachments"][0]; img = r.json()
check(r.status_code == 200 and fake.calls[-1]["url"].endswith("/chats/919811111111@s.whatsapp.net/messages/send"), "A photo goes out through the chat's own send route")
check(sent_body["text"] == "Look at this" and _b64.b64decode(att["content"]) == PNG and att["content_type"] == "image/png" and att["filename"] == "photo.png" and "voice_note" not in att,
      "...as a base64 attachment with its caption, type and file name")
check(img["type"] == "image" and img["text"] == "Look at this" and img["media_name"] == "photo.png" and img["media_type"] == "image/png" and img["status"] == "sent" and img["media_url"].startswith("/api/v1/media/"),
      "The sent photo is stored on the chat with its file details")
got = client.get(img["media_url"])
check(got.status_code == 200 and got.content == PNG and got.headers["content-type"] == "image/png", "The thread can show it: the signed link serves the exact bytes, no login needed")
check(client.get(img["media_url"].replace("t=", "t=0")).status_code == 403, "A tampered link is refused")
bare = img["media_url"].split("?")[0]
check(client.get(bare).status_code == 403, "A link without its signature is refused")
_u = uuid.uuid4()
check(client.get(f"/api/v1/media/{_u}?t={_sign(str(_u))}").status_code == 404, "A correctly signed link to nothing is a 404")

r = upload("price list.pdf", b"%PDF-1.4 fake", "application/pdf")
check(r.json()["type"] == "document" and r.json()["media_name"] == "price list.pdf" and fake.calls[-1]["json"]["attachments"][0]["content_type"] == "application/pdf", "A PDF is sent and shown as a document")
r = upload("clip.mp4", b"\x00\x00\x00\x18ftypmp42", "video/mp4")
check(r.json()["type"] == "video" and fake.calls[-1]["json"]["text"] == "", "A video is sent (no caption)")

# voice note: browser audio -> Ogg/Opus voice message
_real = _inbox.to_ogg_opus
_inbox.to_ogg_opus = lambda d: b"OggS-converted"
r = upload("voice.webm", b"webm-bytes", "audio/webm;codecs=opus", voice="true", duration="7")
att = fake.calls[-1]["json"]["attachments"][0]
check(_b64.b64decode(att["content"]) == b"OggS-converted" and att["content_type"] == "audio/ogg" and att["filename"] == "voice.ogg" and att["voice_note"] is True and att["metadata"] == {"duration": 7},
      "Voice note: converted to Ogg/Opus and flagged as a playable voice message with its length")
check(r.json()["type"] == "voice" and r.json()["duration_seconds"] == 7 and r.json()["media_type"] == "audio/ogg", "...and stored as a voice message on the chat")
_inbox.to_ogg_opus = lambda d: None
r = upload("voice.webm", b"webm-bytes", "audio/webm", voice="true", duration="3")
att = fake.calls[-1]["json"]["attachments"][0]
check(r.status_code == 200 and att["content_type"] == "audio/webm" and att["voice_note"] is True, "No converter available: the recording is still sent as a voice note, unconverted")
_inbox.to_ogg_opus = _real
if _ffmpeg():
    import io as _io, math as _math, struct as _struct, wave as _wave
    buf = _io.BytesIO()
    with _wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(44100)
        w.writeframes(b"".join(_struct.pack("<h", int(9000 * _math.sin(2 * _math.pi * 440 * i / 44100))) for i in range(44100)))
    webm = _sp.run([_ffmpeg(), "-loglevel", "error", "-i", "pipe:0", "-c:a", "libopus", "-f", "webm", "pipe:1"], input=buf.getvalue(), capture_output=True).stdout
    r = upload("voice.webm", webm, "audio/webm;codecs=opus", voice="true", duration="1")
    att = fake.calls[-1]["json"]["attachments"][0]; raw = _b64.b64decode(att["content"])
    check(webm[:4] == b"\x1aE\xdf\xa3" and raw[:4] == b"OggS" and b"OpusHead" in raw and att["content_type"] == "audio/ogg", "Real conversion: a real WebM/Opus recording leaves as a real Ogg/Opus voice note")
else:
    print("[skip] no ffmpeg on this machine: real conversion not exercised")
check(upload("notes.txt", b"just text", "text/plain", voice="true").status_code == 400, "A 'voice note' that is not audio is rejected")

check(upload("empty.png", b"", "image/png").status_code == 400, "An empty file is rejected")
_lim = settings.MAX_UPLOAD_MB; settings.MAX_UPLOAD_MB = 1
check(upload("big.bin", b"x" * (1024 * 1024 + 5), "application/octet-stream").status_code == 413, "A file over the size limit is refused (413)")
settings.MAX_UPLOAD_MB = _lim
stop_conv = next(c["id"] for c in client.get("/api/v1/inbox/conversations", headers=SA).json() if c["contact"]["phone"] == "+919876500002")
check(upload("photo.png", PNG, "image/png", cid=stop_conv).status_code == 400, "No attachments to a customer who replied STOP")
check(upload("photo.png", PNG, "image/png", h=other_h).status_code == 404, "Another admin cannot send into this chat")
fake.fail = True
r = upload("photo.png", PNG, "image/png")
check(r.status_code == 200 and r.json()["status"] == "failed" and "not on WhatsApp" in (r.json()["error_message"] or ""), "A provider failure is shown on the message, not hidden")
fake.fail = False

# received media: shown through a signed proxy that only sends the key to the service itself
def inbound_media(mid, atts, text=""):
    webhook({"type": "message.new", "account_id": "acc_123", "payload": {"id": mid, "chat_id": "919811111111@s.whatsapp.net", "sender_id": "919811111111@s.whatsapp.net",
             "text": text, "is_sender": False, "attachments": atts}})
    with SessionLocal() as db:
        return db.query(Message).filter(Message.provider_message_id == mid).first()
m_img = inbound_media("in_img", [{"type": "img", "mimetype": "image/jpeg", "url": "https://cdn.example.com/files/pic.jpg", "filename": "pic.jpg"}], "my photo")
m_voice = inbound_media("in_voice", [{"type": "audio", "mimetype": "audio/ogg", "url": settings.UNIPILE_BASE_URL + "/files/voice1", "duration": 5000, "voice_note": True}])
m_doc = inbound_media("in_doc", [{"type": "file", "mimetype": "application/pdf", "url": "https://cdn.example.com/files/menu.pdf", "filename": "menu.pdf"}])
check(m_img.type.value == "image" and m_voice.type.value == "voice" and m_doc.type.value == "document" and m_voice.duration_seconds == 5 and m_doc.media_name == "menu.pdf",
      "Received photo, voice note (5000 ms -> 5 s) and document are typed correctly")
check(all(m.media_url.startswith("/api/v1/media/m/") and m.media_source for m in (m_img, m_voice, m_doc)), "...and shown through the app, not the raw service address")
_seen = []
_prev = httpx.get
def _recorder(url, headers=None, **kw):
    _seen.append((url, dict(headers or {})))
    return httpx.Response(200, content=b"FILEBYTES", headers={"content-type": "application/octet-stream"}, request=httpx.Request("GET", url))
httpx.get = _recorder
g1 = client.get(m_img.media_url); g2 = client.get(m_voice.media_url)
httpx.get = _prev
check(g1.status_code == 200 and g1.content == b"FILEBYTES" and g1.headers["content-type"] == "image/jpeg", "A received photo is fetched and served with its type")
check("X-API-KEY" not in _seen[0][1] and _seen[1][1].get("X-API-KEY") == "key_test", "The API key goes only to the service's own address, never to other hosts")
check(client.get(m_img.media_url.replace("t=", "t=1")).status_code == 403, "Received-media links are signed too")
chat_msgs = client.get(f"/api/v1/inbox/conversations/{att_conv}", headers=SA).json()["messages"]
check({"image", "voice", "document", "video"} <= {m["type"] for m in chat_msgs}, "The chat thread lists every attachment type")
# history import carries attachments too
CHATS["acc_456"].append({"id": "919833333333@s.whatsapp.net", "name": "Media Fan", "user_id": "919833333333@s.whatsapp.net", "unread_count": 1})
MESSAGES["919833333333@s.whatsapp.net"] = [_m("hist_1", "", "2026-11-10T12:00:00.000Z", False, attachments=[{"type": "img", "mimetype": "image/png", "url": "https://cdn.example.com/h.png", "filename": "h.png"}])]
with SessionLocal() as db:
    sync.sync_history(db, db.query(WhatsAppNumber).filter(WhatsAppNumber.unipile_account_id == "acc_456").first())
    hm = db.query(Message).filter(Message.provider_message_id == "hist_1").first()
    check(hm is not None and hm.type.value == "image" and hm.media_url.startswith("/api/v1/media/m/"), "Imported history keeps photos too")

# --- saved message templates (plain text, no provider involved) --------------------
tpl = lambda h, **kw: client.post("/api/v1/templates", headers=h, json=kw)
r = tpl(ADMIN, name="Opening hours", text="Hi {{name}}, we are open 9-6, Mon-Sat.")
saved = r.json()
check(r.status_code == 200 and saved["admin_id"] == rahul_id and saved["name"] == "Opening hours", "An Admin saves a message template")
check([t["name"] for t in client.get("/api/v1/templates", headers=ADMIN).json()] == ["Opening hours"], "...and lists their own")
check(client.get("/api/v1/templates", headers=other_h).json() == [], "Another Admin does not see it")
check(client.patch(f"/api/v1/templates/{saved['id']}", headers=other_h, json={"name": "x", "text": "y"}).status_code == 404
      and client.delete(f"/api/v1/templates/{saved['id']}", headers=other_h).status_code == 404, "...and cannot change or delete it")
check(any(t["id"] == saved["id"] for t in client.get("/api/v1/templates", headers=SA).json()), "A Super Admin sees every template")
check(tpl(ADMIN, name="", text="x").status_code == 422 and tpl(ADMIN, name="x", text="").status_code == 422 and tpl(ADMIN, name="  ", text="  ").status_code == 400, "Empty names or texts are refused")
r = client.patch(f"/api/v1/templates/{saved['id']}", headers=ADMIN, json={"name": "Hours", "text": "We open at 9."})
check(r.status_code == 200 and r.json()["name"] == "Hours" and r.json()["text"] == "We open at 9.", "A template can be edited")
check(client.delete(f"/api/v1/templates/{saved['id']}", headers=ADMIN).status_code == 204 and client.get("/api/v1/templates", headers=ADMIN).json() == [], "...and deleted")

# --- keyword automations: started by the incoming-message webhook ---------------------
def kw_customer(text="hello"):
    only_running(None)                                   # no automation runs while the contact is created
    return new_customer(text)
kw = _make("keyword_reply").json()
check(kw["trigger_type"] == "keyword" and "price" in kw["trigger_config"]["keywords"] and kw["status"] == "Scheduled", "The keyword template creates a keyword automation")
c1 = kw_customer()
only_running(kw["id"]); n0 = len(fake.calls)
inbound("kw_1", "Hi, what is the PRICE of the large one?", c1); ar.process_due_runs()
check(len(fake.calls) == n0 + 1 and fake.calls[-1]["json"]["text"].startswith("Hi Asha, thanks for asking!"), "A message containing a keyword (any case) gets the automatic reply")
inbound("kw_2", "and the price for two?", c1); ar.process_due_runs()
check(len(fake.calls) == n0 + 1, "...but only once per chat per day")
c2 = kw_customer(); only_running(kw["id"]); n1 = len(fake.calls)
inbound("kw_3", "that is pricey", c2); ar.process_due_runs()
inbound("kw_4", "no keyword here", c2); ar.process_due_runs()
check(len(fake.calls) == n1, "Whole words only ('pricey') and no keyword: no reply")
inbound("kw_5", "Pricing please", c2); ar.process_due_runs()
check(len(fake.calls) == n1 + 1, "A different customer asking about 'pricing' is answered")
only_running(None); n2 = len(fake.calls); c3 = kw_customer()
inbound("kw_6", "price?", c3); ar.process_due_runs()
check(len(fake.calls) == n2, "A paused keyword automation does nothing")
only_running(kw["id"]); c4 = kw_customer("STOP"); only_running(kw["id"]); n3 = len(fake.calls)
inbound("kw_7", "price", c4); ar.process_due_runs()
check(len(fake.calls) == n3, "A customer who replied STOP is never auto-replied")
with SessionLocal() as db:
    check(db.get(Automation, uuid.UUID(kw["id"])).recipients == 2 and db.get(Automation, uuid.UUID(kw["id"])).sent == 2, "The automation counts who it answered")

hk = _make("keyword_human").json(); only_running(None); c5 = kw_customer(); only_running(hk["id"]); n4 = len(fake.calls)
inbound("kw_8", "I need HELP with my order", c5); ar.process_due_runs()
check(len(fake.calls) == n4 + 1 and "member of our team" in fake.calls[-1]["json"]["text"] and any("handed this chat to a human" in (t or "") for t in notes(c5)), "'help' hands the chat to a human after a confirmation")

# create/edit keyword automations by hand
mk = lambda **kw_: client.post("/api/v1/automations", headers=ADMIN, json={"name": "Mine", **kw_})
check(mk(trigger_type="keyword").status_code == 422 and mk(trigger_type="keyword", trigger_config={"keywords": ["  "]}).status_code == 422, "A keyword automation needs keywords")
check(mk(trigger_type="telepathy").status_code == 422, "Unknown triggers are refused")
mine = mk(trigger_type="keyword", trigger_config={"keywords": [" menu ", "hours"]}, flow={"nodes": [{"id": "trigger", "type": "trigger", "label": "Message contains keywords"}, {"id": "r", "type": "action", "label": "Send reply", "messageVariants": ["Open 9-6, {{name}}"]}]})
check(mine.status_code == 200 and mine.json()["trigger_config"]["keywords"] == ["menu", "hours"], "Keywords are trimmed and saved")
up = client.patch(f"/api/v1/automations/{mine.json()['id']}", headers=ADMIN, json={"trigger_type": "keyword", "trigger_config": {"keywords": ["menu", "location"]}})
check(up.status_code == 200 and up.json()["trigger_config"]["keywords"] == ["menu", "location"], "The keywords can be changed later")
check(client.patch(f"/api/v1/automations/{mine.json()['id']}", headers=ADMIN, json={"trigger_type": "keyword", "trigger_config": {}}).status_code == 422, "...but not emptied")
only_running(None)

# --- automated replies wait a random 15-30 seconds -------------------------------------
from datetime import timedelta as _td3  # noqa: E402
from app.services.automation_runner import reply_delay as _reply_delay  # noqa: E402
settings.AUTOMATION_REPLY_DELAY_MIN, settings.AUTOMATION_REPLY_DELAY_MAX = 15, 30
check(client.get("/api/v1/system/whatsapp", headers=ADMIN).json()["reply_delay"] == {"min": 15.0, "max": 30.0}, "The default wait (15-30 s) is reported to the app")

def pending_waits():
    with SessionLocal() as db:
        rows = db.query(AutomationRun).filter(AutomationRun.status == "pending").all()
        return [((_aw(r.run_at) - _aw(r.started_at)).total_seconds(), r) for r in rows], [(r.next_index, r.jitter_done, r.branch_queue) for r in rows]
def _aw(d):
    return d.replace(tzinfo=_tz.utc) if d.tzinfo is None else d

welcome = _make("welcome").json(); only_running(welcome["id"]); n0 = len(fake.calls)
waits = []
for _ in range(20):
    sender = f"91986{next(_n):07d}@s.whatsapp.net"
    inbound("dl_" + sender.split("@")[0][-9:], "hello", sender)
ar.process_due_runs()
waits = [w for w, _ in pending_waits()[0]]
check(len(fake.calls) == n0 and len(waits) == 20, "20 new customers: nothing is sent at the moment they write", f"sent {len(fake.calls) - n0}, waiting {len(waits)}")
check(all(15 <= w <= 30.001 for w in waits), "Each reply is scheduled between 15 and 30 seconds later", f"min {min(waits):.1f} max {max(waits):.1f}")
check(max(waits) - min(waits) > 4, "...and the delays are random, not one fixed number", f"spread {max(waits) - min(waits):.1f}s")
ar.process_due_runs()
check(len(fake.calls) == n0, "Polling again before the time has come still sends nothing")
fast_forward(); ar.process_due_runs()
check(len(fake.calls) == n0 + 20 and all("Thanks for contacting us" in c["json"]["text"] for c in fake.calls[n0:]), "Once the wait is over, each welcome is sent (exactly once)")
fast_forward(); ar.process_due_runs()
check(len(fake.calls) == n0 + 20, "...and never again")

# follow-up inside the YES/NO branch waits too; the hand-over after a message does not need to
only_running(_make("welcome_followup").json()["id"]); n1 = len(fake.calls); silent = new_customer()
check(len(fake.calls) == n1, "Welcome + follow-up: the welcome waits")
fast_forward(); ar.process_due_runs()
check(len(fake.calls) == n1 + 1, "...then goes out")
fast_forward(); ar.process_due_runs()          # the 2-hour wait is over -> 'replied?' -> NO branch
q = pending_waits()[1]
check(len(fake.calls) == n1 + 1 and q and q[0][2], "After the 2-hour wait, the follow-up is queued but waits its own 15-30 s first")
fast_forward(); ar.process_due_runs()
check(len(fake.calls) == n1 + 2 and "just checking in" in fake.calls[-1]["json"]["text"], "...then the follow-up is sent")

only_running(_make("handoff").json()["id"]); n2 = len(fake.calls); h = new_customer()
check(len(fake.calls) == n2 and not any("handed this chat" in (t or "") for t in notes(h)), "Hand over: the acknowledgement waits, so the hand-over waits behind it")
fast_forward(); ar.process_due_runs()
check(len(fake.calls) == n2 + 1 and any("handed this chat to a human" in (t or "") for t in notes(h)), "...then the acknowledgement goes out and the chat is handed over together")

# keyword replies wait as well
kwa = _make("keyword_reply").json(); only_running(None); kc = new_customer(); only_running(kwa["id"]); n3 = len(fake.calls)
inbound("dl_kw", "what is the price?", kc); ar.process_due_runs()
check(len(fake.calls) == n3, "A keyword auto-reply waits too")
fast_forward(); ar.process_due_runs()
check(len(fake.calls) == n3 + 1, "...and then arrives")

# per-step override and sane bounds
check(_reply_delay({}) == (15.0, 30.0) and _reply_delay({"replyDelay": {"min": 5, "max": 8}}) == (5.0, 8.0) and _reply_delay({"replyDelay": {"min": 0, "max": 0}}) == (0.0, 0.0),
      "A step can set its own wait (or 0 for instant)")
check(_reply_delay({"replyDelay": {"min": 20, "max": 5}}) == (20.0, 20.0) and _reply_delay({"replyDelay": {"min": "x", "max": None}}) == (15.0, 30.0)
      and _reply_delay({"replyDelay": {"min": 0, "max": 99999}}) == (0.0, 3600.0), "Nonsense values fall back to safe ones")
fast_flow = {"nodes": [{"id": "trigger", "type": "trigger", "label": "New customer"},
                       {"id": "w", "type": "action", "label": "Send welcome message", "messageVariants": ["Instant {{name}}"], "replyDelay": {"min": 0, "max": 0}}]}
only_running(client.post("/api/v1/automations", headers=ADMIN, json={"name": "Instant", "flow": fast_flow}).json()["id"]); n4 = len(fake.calls); new_customer()
check(len(fake.calls) == n4 + 1 and fake.calls[-1]["json"]["text"] == "Instant Asha", "A step with replyDelay 0/0 sends immediately")
five = {"nodes": [fast_flow["nodes"][0], {**fast_flow["nodes"][1], "replyDelay": {"min": 5, "max": 5}}]}
only_running(client.post("/api/v1/automations", headers=ADMIN, json={"name": "Five", "flow": five}).json()["id"]); n5 = len(fake.calls); new_customer()
w5 = pending_waits()[0]
check(len(fake.calls) == n5 and len(w5) == 1 and 4.9 <= w5[0][0] <= 5.1, "A step with replyDelay 5/5 waits 5 seconds")
only_running(None)
settings.AUTOMATION_REPLY_DELAY_MIN, settings.AUTOMATION_REPLY_DELAY_MAX = 0, 0

# --- hidden-number chats, system chats, cleanup -------------------------------------
from app.models.contact import Contact  # noqa: E402
from app.services.campaign_runner import resolve_audience  # noqa: E402
nop_id = next(c["id"] for c in client.get("/api/v1/inbox/conversations", headers=SA).json() if c["contact"]["phone"] == "lid:nophone")
r = client.post(f"/api/v1/inbox/conversations/{nop_id}/messages", headers=SA, json={"type": "text", "text": "hello you"})
check(r.json()["status"] == "sent" and fake.calls[-1]["url"] == "https://api.unipile.com/v2/acc_123/chats/nophone@lid/messages/send",
      "An imported hidden-number chat can be answered (reply goes to its own chat id)")
r = upload("pic.png", PNG, "image/png", cid=nop_id)
check(fake.calls[-1]["url"].endswith("/chats/nophone@lid/messages/send") and r.status_code == 200, "...with attachments too")
with SessionLocal() as db:
    n123 = db.query(WhatsAppNumber).filter(WhatsAppNumber.unipile_account_id == "acc_123").first()
    check(not any((c.phone or "").startswith("lid:") for c in resolve_audience(db, n123, {})[0]), "Campaigns never dial a hidden-number contact (they can only be answered in their chat)")
    # leftovers from an earlier version: WhatsApp's own chat imported as "WhatsApp Business +0"
    ghost = Contact(name="WhatsApp Business", phone="+0", whatsapp_number_id=n123.id, admin_id=n123.admin_id, tags=["WhatsApp"], source="Existing chat")
    db.add(ghost); db.flush()
    gconv = Conversation(contact_id=ghost.id, whatsapp_number_id=n123.id, admin_id=n123.admin_id, provider_chat_id="0@s.whatsapp.net")
    db.add(gconv); db.flush()
    db.add(Message(conversation_id=gconv.id, sender=MessageSender.CUSTOMER, type=MessageType.TEXT, text="Welcome to WhatsApp Business", status=MessageStatus.DELIVERED))
    db.commit()
    total = db.query(Conversation).count()
    check(sync.purge_system_chats(db) == 1 and db.query(Conversation).count() == total - 1 and db.query(Contact).filter(Contact.phone == "+0").count() == 0,
          "A leftover 'WhatsApp Business +0' chat from the old importer is removed automatically")
    check(sync.purge_system_chats(db) == 0, "...and real chats are untouched")

# --- real chats are never lost: unknown id formats, replied chats, recovery -----------
from app.services.chat_utils import is_system_chat as _is_sys  # noqa: E402
from app.models.automation import AutomationRun as _AR  # noqa: E402
check(all(_is_sys(i) for i in ("0@s.whatsapp.net", "120363111@g.us", "status@broadcast", "123@newsletter", "", None, "123@s.whatsapp.net")),
      "Only definite non-customers count as system chats (WhatsApp's own account, groups, status, newsletters)")
check(not any(_is_sys(i) for i in ("919811111111@s.whatsapp.net", "148257303814325@lid", "ch_8f2a91", "919811111111@c.us", "1234567@s.whatsapp.net", "AbC-123")),
      "An id in ANY other format is a customer: nothing real is ever skipped or removed because of its shape")

with SessionLocal() as db:
    n456 = db.query(WhatsAppNumber).filter(WhatsAppNumber.unipile_account_id == "acc_456").first()
    # the scenario that lost chats: a real chat whose id is not a '@s.whatsapp.net' / '@lid' id, and one the team replied in
    odd = Contact(name="Odd Id Customer", phone="+919855550001", whatsapp_number_id=n456.id, admin_id=n456.admin_id, tags=["WhatsApp"])
    db.add(odd); db.flush()
    odd_conv = Conversation(contact_id=odd.id, whatsapp_number_id=n456.id, admin_id=n456.admin_id, provider_chat_id="ch_8f2a91")
    db.add(odd_conv); db.flush()
    db.add(Message(conversation_id=odd_conv.id, sender=MessageSender.CUSTOMER, type=MessageType.TEXT, text="my real question", status=MessageStatus.DELIVERED))
    db.add(Message(conversation_id=odd_conv.id, sender=MessageSender.BUSINESS, type=MessageType.TEXT, text="our real answer", status=MessageStatus.READ))
    replied_sys = Contact(name="Replied here", phone="+919855550002", whatsapp_number_id=n456.id, admin_id=n456.admin_id, tags=["WhatsApp"])
    db.add(replied_sys); db.flush()
    sys_conv = Conversation(contact_id=replied_sys.id, whatsapp_number_id=n456.id, admin_id=n456.admin_id, provider_chat_id="120363777@g.us")
    db.add(sys_conv); db.flush()
    db.add(Message(conversation_id=sys_conv.id, sender=MessageSender.BUSINESS, type=MessageType.TEXT, text="we wrote here", status=MessageStatus.SENT))
    db.commit()
    check(sync.purge_system_chats(db) == 0 and db.get(Conversation, odd_conv.id) is not None, "The clean-up leaves a chat with an unrecognised id format alone")
    check(db.get(Conversation, sys_conv.id) is not None and db.query(Message).filter(Message.conversation_id == sys_conv.id).count() == 1, "...and even a system-looking chat is kept if the team ever replied in it")

    # a chat whose id has another shape is imported, and answered at that id
    CHATS["acc_456"].append({"id": "ch_odd9", "name": "Odd Id Person", "user_id": "919855550009@s.whatsapp.net", "unread_count": 0})
    CHATS["acc_456"].append({"id": "ch_grp9", "name": "Some group", "user_id": None, "type": "group", "unread_count": 0})
    MESSAGES["ch_odd9"] = [_m("odd_1", "hello from an odd id", "2026-11-10T13:00:00.000Z")]
    MESSAGES["ch_grp9"] = [_m("grp_9", "group talk", "2026-11-10T13:01:00.000Z")]
    sync.sync_history(db, n456)
    oc = db.query(Conversation).filter(Conversation.provider_chat_id == "ch_odd9").first()
    check(oc is not None and db.get(Contact, oc.contact_id).phone == "+919855550009", "A chat with an unusual id is imported (the person's phone comes from the person id)")
    check(db.query(Conversation).filter(Conversation.provider_chat_id == "ch_grp9").count() == 0, "...while a chat the service marks as a group is not")
oc_id = str(oc.id)
client.post(f"/api/v1/inbox/conversations/{oc_id}/messages", headers=SA, json={"type": "text", "text": "reply to odd"})
check(fake.calls[-1]["url"].endswith("/acc_456/chats/ch_odd9/messages/send"), "...and you can reply: it goes to that exact chat id")

# every chat of a number disappears -> they come back from the service by themselves, without duplicates
def wait_idle():
    for _ in range(80):
        if not sync.STATE["history_importing"]:
            return
        __import__("time").sleep(0.1)
with SessionLocal() as db:
    n456 = db.query(WhatsAppNumber).filter(WhatsAppNumber.unipile_account_id == "acc_456").first()
    ids = [c.id for c in db.query(Conversation).filter(Conversation.whatsapp_number_id == n456.id).all()]
    cids = [c.contact_id for c in db.query(Conversation).filter(Conversation.whatsapp_number_id == n456.id).all()]
    expected_chats = len([c for c in CHATS["acc_456"] if c["id"] in MESSAGES and not c.get("type") == "group" and c["id"] not in ("0@s.whatsapp.net",)])
    db.query(_AR).filter(_AR.conversation_id.in_(ids)).delete(synchronize_session=False)
    db.query(Message).filter(Message.conversation_id.in_(ids)).delete(synchronize_session=False)
    db.query(Conversation).filter(Conversation.id.in_(ids)).delete(synchronize_session=False)
    db.query(Contact).filter(Contact.id.in_(cids)).delete(synchronize_session=False)
    n456.history_synced_at = _dt.now(_tz.utc)      # it still says 'already imported'
    db.commit()
    check(db.query(Conversation).filter(Conversation.whatsapp_number_id == n456.id).count() == 0, "(simulated) every chat of one number is gone")
sync._chats_step(); wait_idle()
with SessionLocal() as db:
    back = db.query(Conversation).filter(Conversation.whatsapp_number_id == n456.id).count()
    check(back >= 3 and back == len({m.conversation_id for m in db.query(Message).join(Conversation, Conversation.id == Message.conversation_id).filter(Conversation.whatsapp_number_id == n456.id)}),
          "A number that says 'imported' but has no chats is imported again automatically", str(back))

# 'Re-import chats': reads everything again from the service, deletes nothing, never duplicates
with SessionLocal() as db:
    msgs_before = db.query(Message).count(); convs_before = db.query(Conversation).count()
check(client.post("/api/v1/numbers/refresh?full=true", headers=ADMIN).status_code == 403, "Only a Super Admin can re-import")
r = client.post("/api/v1/numbers/refresh?full=true", headers=SA).json()
wait_idle()
for _ in range(80):
    if not sync._work_lock.locked():
        break
    __import__("time").sleep(0.1)
with SessionLocal() as db:
    check(r.get("ok") and r.get("reimport") and db.query(Message).count() == msgs_before and db.query(Conversation).count() == convs_before,
          "Re-import chats: everything is read again, nothing is removed and nothing is duplicated", f"{db.query(Message).count()} vs {msgs_before}; {db.query(Conversation).count()} vs {convs_before}")

# --- following the Unipile guide: phone-sent messages, no duplicates, "Find Chats by User" ------
oc = client.post("/api/v1/inbox/conversations/" + conv["id"] + "/messages", headers=ADMIN, json={"type": "text", "text": "Sent from the app"}).json()
inbound("own_twin", "Sent from the app", sender="919876500001@s.whatsapp.net", is_sender=True)
twins = [m for m in client.get(f"/api/v1/inbox/conversations/{conv['id']}", headers=ADMIN).json()["messages"] if m["text"] == "Sent from the app"]
check(len(twins) == 1, "A message sent from this app is not shown twice when Unipile reports it under another id")
fake.fixed_id = "FIXED_X"
inbound("FIXED_X", "Phone first", sender="919876500001@s.whatsapp.net", is_sender=True)
r = client.post("/api/v1/inbox/conversations/" + conv["id"] + "/messages", headers=ADMIN, json={"type": "text", "text": "Phone first"}).json()
fake.fixed_id = None
fx = [m for m in client.get(f"/api/v1/inbox/conversations/{conv['id']}", headers=ADMIN).json()["messages"] if m["text"] == "Phone first"]
check(len(fx) == 1 and r["id"] == fx[0]["id"], "...nor when Unipile's notification arrives before the app has stored its own copy")
inbound("own_new", "Hello, first contact from my phone", sender="919877001122@s.whatsapp.net", is_sender=True)
newc = next((c for c in client.get("/api/v1/inbox/conversations", headers=ADMIN).json() if c["contact"]["phone"] == "+919877001122"), None)
check(newc is not None and newc["unread_count"] == 0, "Writing to someone new from the phone creates their chat here (nothing unread)")
inbound("m_back", "Hi! got your message", sender="919877001122@s.whatsapp.net")
check(next(c for c in client.get("/api/v1/inbox/conversations", headers=ADMIN).json() if c["contact"]["phone"] == "+919877001122")["unread_count"] == 1, "...and their reply is threaded with it")

svc = ws.get_whatsapp_service()
fake.user_chats.clear(); n_get = len(fake.gets); n_call = len(fake.calls)
res = svc.send_text("+919876543210", "+919800123456", "no chat yet")
check(res.success and fake.gets[n_get:] == ["https://api.unipile.com/v2/acc_123/users/919800123456@s.whatsapp.net/chat"]
      and fake.calls[-1]["url"] == "https://api.unipile.com/v2/acc_123/chats/send" and fake.calls[-1]["json"]["users_ids"] == "919800123456@s.whatsapp.net",
      "Sending to someone with no stored chat asks 'Find Chats by User' first, then starts a chat with number@s.whatsapp.net")
fake.user_chats["919800123456@s.whatsapp.net"] = "chat_found_77"
res = svc.send_text("+919876543210", "+919800123456", "chat exists")
check(res.success and fake.calls[-1]["url"] == "https://api.unipile.com/v2/acc_123/chats/chat_found_77/messages/send", "...and reuses the chat Unipile says already exists, whatever its id looks like")
fake.user_chats.clear()

# --- "Check connection": why texts might not arrive --------------------------------------
import app.api.v1.endpoints.unipile as _unip  # noqa: E402
from app.services import webhook_stats as _wh  # noqa: E402
OUR_URL = "https://crm.example.com/api/v1/whatsapp/webhook"
GOOD_EVENTS = ["message.new", "message.receipt.delivery", "message.receipt.read", "account.status.disconnected", "account.remove"]
def good_endpoint(**kw):
    return {"object": "WebhookEndpoint", "id": "we_1", "url": OUR_URL, "enabled": True, "trigger_events": list(GOOD_EVENTS), "account_ids": [], "secret": SECRET, **kw}
def deliveries(*codes):
    return [{"object": "WebhookConversation", "id": f"wc_{i}", "endpoint_id": "we_1", "http_status": c, "first_attempt": True} for i, c in enumerate(codes)]
def _route_post(url, content=None, headers=None, timeout=None, **kw):
    if url.startswith("https://crm.example.com"):
        rr = client.post(url.replace("https://crm.example.com", ""), content=content, headers=headers)
        return httpx.Response(rr.status_code, content=rr.content, request=httpx.Request("POST", url))
    raise httpx.ConnectError("blocked")
_real_post = httpx.post
httpx.post = _route_post
def check_conn(h=SA):
    r = client.post("/api/v1/system/connection-check", headers=h)
    return r, {c["key"]: c for c in (r.json().get("checks", []) if r.status_code == 200 else [])}
settings.UNIPILE_ACCOUNT_IDS = ""
PROVIDER["all_fail"] = PROVIDER["list_fail"] = None
ACCOUNTS[:] = [a for a in ACCOUNTS if a["id"] in ("acc_123", "acc_456", "acc_789")]
for a in ACCOUNTS:
    a["status"] = "running"

for _k in ("rejected_signature", "rejected_stale", "unknown_account"):
    _wh.STATS[_k] = 0      # a clean server: nothing refused yet
_wh.STATS["last_rejected_at"] = None
ENDPOINTS[:] = [good_endpoint()]; DELIVERIES[:] = deliveries(200, 200, 200)
recv_before = _wh.STATS["received"]
r, c = check_conn()
check(r.status_code == 200 and r.json()["webhook_url"] == OUR_URL and all(x["status"] in ("ok", "warn") for x in c.values()), "Healthy setup: every check passes", str({k: v["status"] for k, v in c.items() if v["status"] not in ("ok",)}))
check(all(c[k]["status"] == "ok" for k in ("live", "key", "address", "endpoint", "events", "coverage", "secretmatch", "deliveries")), "...key, address, endpoint, events, coverage, secret and deliveries each reported OK")
check(_wh.STATS["selftest_at"] and _wh.STATS["received"] == recv_before, "The signed self-test reached the endpoint but is not counted as a real notification")
check("SECRET" not in json.dumps(r.json()) and SECRET not in json.dumps(r.json()), "The secret is never shown")
check(client.post("/api/v1/system/connection-check", headers=ADMIN).status_code == 403, "Only a Super Admin can run it")

ENDPOINTS[:] = [good_endpoint(url="https://elsewhere.example.org/hook")]
r, c = check_conn()
check(c["endpoint"]["status"] == "fail" and "elsewhere.example.org" in c["endpoint"]["detail"] and OUR_URL in c["endpoint"]["fix"], "No endpoint at this server's address: says what exists and the exact address to add")
ENDPOINTS[:] = [good_endpoint(enabled=False, trigger_events=["email.new"], account_ids=["acc_123"], secret="something-else")]
r, c = check_conn()
check(c["endpoint"]["status"] == "fail" and "switched off" in c["endpoint"]["detail"], "A disabled endpoint is flagged")
check(c["events"]["status"] == "fail" and "message.new" in c["events"]["detail"], "An endpoint not subscribed to new messages is flagged (texts would never be announced)")
check(c["coverage"]["status"] == "fail" and "Not covered" in c["coverage"]["detail"], "An endpoint limited to some accounts names the numbers it misses")
check(c["secretmatch"]["status"] == "fail" and "DIFFERENT" in c["secretmatch"]["detail"] and "something-else" not in json.dumps(r.json()), "A secret that differs from the server's is flagged, without revealing either")
ENDPOINTS[:] = [good_endpoint()]; DELIVERIES[:] = deliveries(200, 403, 403, None, 200)
r, c = check_conn()
check(c["deliveries"]["status"] == "fail" and "HTTP 403 x2" in c["deliveries"]["detail"] and "timeout" in c["deliveries"]["detail"], "Failing deliveries are summarised (HTTP 403 means a secret mismatch, no answer means a timeout)")
DELIVERIES[:] = []
r, c = check_conn()
check(c["deliveries"]["status"] == "warn", "No delivery attempts yet is a warning, not an error")
ACCOUNTS[1]["status"] = "disconnected"
r, c = check_conn()
check(c["acc:acc_456"]["status"] == "fail" and "Not connected" in c["acc:acc_456"]["detail"] and c["acc:acc_123"]["status"] == "ok", "A disconnected number is named")
ACCOUNTS[1]["status"] = "running"

PROVIDER["all_fail"] = 401
r, c = check_conn()
check(r.status_code == 200 and c["key"]["status"] == "fail" and "401" in c["key"]["detail"] and c["endpoint"]["status"] == "warn", "A rejected key is reported and the rest still runs")
PROVIDER["all_fail"] = None
httpx.post = lambda *a, **k: (_ for _ in ()).throw(httpx.ConnectError("down"))
r, c = check_conn()
check(c["address"]["status"] == "fail" and "Could not reach" in c["address"]["detail"], "An address the server cannot reach is flagged")
httpx.post = _route_post
_ps = settings.PUBLIC_BASE_URL; settings.PUBLIC_BASE_URL = ""
r, c = check_conn()
check(c["address"]["status"] == "fail" and "public address is not set" in c["address"]["detail"], "A missing public address is flagged")
settings.PUBLIC_BASE_URL = _ps

# what the endpoint itself has seen
bad_before = _wh.STATS["rejected_signature"]
webhook({"type": "message.new", "account_id": "acc_123", "payload": {"id": "zz"}}, sign=False)
check(_wh.STATS["rejected_signature"] == bad_before + 1 and _wh.STATS["last_rejected_at"], "Notifications with a wrong signature are counted")
_unip._last_import[0] = __import__("time").time()   # keep the background import out of this test
unk = _wh.STATS["unknown_account"]
webhook({"type": "message.new", "account_id": "acc_zzz", "payload": {"id": "zz2"}})
check(_wh.STATS["unknown_account"] == unk + 1 and _wh.STATS["last_unknown_account"] == "acc_zzz", "A notification for a number that is not imported is remembered, not silently dropped")
sysw = client.get("/api/v1/system/whatsapp", headers=SA).json()
check(sysw["webhook"]["received"] >= 1 and sysw["webhook"]["last_type"] == "message.new" and client.get("/api/v1/system/whatsapp", headers=ADMIN).json()["webhook"] is None,
      "The status shows the Super Admin when notifications last arrived (Admins don't see it)")
r, c = check_conn()
check(c["seen"]["status"] == "ok" and c["rejected"]["status"] == "fail" and c["unknown"]["status"] == "warn", "The check reports what the server really received and refused")
httpx.post = _real_post

# --- "Retrieve messages": pull the latest now, from the Inbox, any role ---------------
_far = (_dt.now(_tz.utc) + _td3(minutes=5)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
MESSAGES["919811111111@s.whatsapp.net"].insert(0, _m("ret_1", "Missed by every notification", _far))   # the chat's own timestamp is left stale on purpose
thread_texts = lambda: [m["text"] for m in client.get(f"/api/v1/inbox/conversations/{byphone['+919811111111']['id']}", headers=SA).json()["messages"]]
check("Missed by every notification" not in thread_texts(), "(setup) a message exists at the service that the app has not seen")
r = client.post("/api/v1/inbox/retrieve", headers=SA).json()
check(r["ok"] and r["new_messages"] == 1 and r["chats"] == 1 and r["numbers"] >= 3 and not r["errors"] and "Missed by every notification" in thread_texts(),
      "Retrieve messages finds it and puts it in the chat", str(r))
r = client.post("/api/v1/inbox/retrieve", headers=SA).json()
check(r["ok"] and r["new_messages"] == 0, "...and pressing again finds nothing new (no duplicates)", str(r))
check(client.post("/api/v1/inbox/retrieve", headers=other_h).json()["numbers"] == 0, "An Admin without numbers retrieves nothing (only their own numbers are read)")
mine_n = client.post("/api/v1/inbox/retrieve", headers=ADMIN).json()
check(mine_n["ok"] and 1 <= mine_n["numbers"] <= 3, "An Admin retrieves for their own numbers", str(mine_n))
check(client.post(f"/api/v1/inbox/retrieve?number_id={num2['id']}", headers=other_h).status_code == 404, "...and cannot ask for a number that is not theirs")
only_one = client.post(f"/api/v1/inbox/retrieve?number_id={num['id']}", headers=SA).json()
check(only_one["numbers"] == 1, "One number can be retrieved on its own (the Inbox button follows the number filter)")
PROVIDER["all_fail"] = 401
bad = client.post("/api/v1/inbox/retrieve", headers=SA).json()
check(bad["ok"] and bad["errors"] and "401" in bad["errors"][0]["error"] and "UNIPILE" not in bad["errors"][0]["error"], "When the service refuses, each number's error is reported in plain words")
PROVIDER["all_fail"] = None
sync._work_lock.acquire()
busy = sync.retrieve_recent_messages(None, wait_seconds=0.1)
sync._work_lock.release()
check(busy["ok"] and busy["busy"] and "already being loaded" in busy["message"], "If a background load is running it says so instead of colliding with it")
_key = settings.UNIPILE_API_KEY; settings.UNIPILE_API_KEY = ""
off = client.post("/api/v1/inbox/retrieve", headers=SA).json()
settings.UNIPILE_API_KEY = _key
check(off["ok"] is False and "not connected" in off["error"] and "UNIPILE" not in off["error"], "Without an API key it says WhatsApp is not connected")
check(client.post("/api/v1/inbox/retrieve").status_code == 401, "It needs a sign-in")

# --- two separate keys: API key for chats and sending, Service Key for webhook settings ----
def _settings_with(env):
    saved = {k: os.environ.pop(k) for k in list(os.environ) if k.upper().startswith("UNIPILE") and k.upper() not in ("UNIPILE_BASE_URL",)}
    os.environ.update(env)
    try:
        return Settings(_env_file=None)
    finally:
        for k in env:
            os.environ.pop(k, None)
        os.environ.update(saved)
both = _settings_with({"UNIPILE_API_KEY": "api_one", "UNIPILE_SERVICE_KEY": "svc_two"})
check(both.UNIPILE_API_KEY == "api_one" and both.UNIPILE_SERVICE_KEY == "svc_two" and both.management_key == "svc_two",
      "With both set they stay separate: the API key is the API key, the Service Key manages webhook settings")
only_api = _settings_with({"UNIPILE_API_KEY": "api_one"})
check(only_api.UNIPILE_SERVICE_KEY == "" and only_api.management_key == "api_one", "Without a Service Key, webhook settings are read with the API key")
only_svc = _settings_with({"UNIPILE_SERVICE_KEY": "svc_two"})
check(only_svc.UNIPILE_API_KEY == "svc_two" and only_svc.management_key == "svc_two", "A Service Key alone is used for everything (it can do all an API key can)")
check(_settings_with({"UNIPILE_API_KEY": '  "quoted_key" \n', "UNIPILE_SERVICE_KEY": " svc \n"}).UNIPILE_API_KEY == "quoted_key", "Spaces, line breaks and quotes pasted around a key are removed")
check(_settings_with({"UNIPILE_SERVICE_KEY": "x", "WHATSAPP_MOCK": "true"}).WHATSAPP_MOCK is False and _settings_with({}).UNIPILE_API_KEY == "", "A key always forces live mode; no key stays empty")
settings.UNIPILE_SERVICE_KEY = "svc_test"; KEYS_USED.clear()
httpx.post = _route_post
ENDPOINTS[:] = [good_endpoint()]; DELIVERIES[:] = deliveries(200)
check_conn()
hook_keys = {k for pth, k in KEYS_USED if pth.startswith("/webhooks")}
acct_keys = {k for pth, k in KEYS_USED if pth == "/accounts"}
check(hook_keys == {"svc_test"} and "key_test" in acct_keys, "Check connection reads webhook settings with the Service Key and still reads accounts with the API key", f"{hook_keys} {acct_keys}")
settings.UNIPILE_SERVICE_KEY = ""; KEYS_USED.clear()
check_conn()
check({k for pth, k in KEYS_USED if pth.startswith("/webhooks")} == {"key_test"}, "...and falls back to the API key when no Service Key is saved")
httpx.post = _real_post
_k = settings.UNIPILE_API_KEY; settings.UNIPILE_API_KEY = ""
os.environ["UNIPILE_SERVICE_KEYY"] = "typo"
rr = client.post("/api/v1/system/connection-check", headers=SA).json()
del os.environ["UNIPILE_SERVICE_KEYY"]; settings.UNIPILE_API_KEY = _k
live_c = rr["checks"][0]
check(live_c["status"] == "fail" and "UNIPILE_SERVICE_KEYY" in live_c["detail"] and "UNIPILE_API_KEY" in live_c["fix"], "With no key, the connection check lists settings with a similar name so a misnamed one is obvious")

# --- the API-keys page: expired keys and Scoped keys ------------------------------------
PROVIDER["all_fail"] = 401; PROVIDER["body"] = {"type": "api/expired_authorization", "title": "Expired"}
exp = sync.refresh_now()
check(exp["ok"] is False and "EXPIRED" in exp["error"] and "replacement" in exp["error"] and "UNIPILE" not in exp["error"], "An expired key (401 api/expired_authorization) is named as expired, with what to do")
PROVIDER["body"] = {"detail": "nope"}
plain = sync.refresh_now()
check("EXPIRED" not in plain["error"] and "rejected" in plain["error"], "...while an ordinary 401 still says the key was rejected")
PROVIDER["all_fail"] = None
settings.UNIPILE_SERVICE_KEY = "svc_test"; VISIBLE_FOR_KEY["key_test"] = ["acc_123"]
httpx.post = _route_post
ENDPOINTS[:] = [good_endpoint()]; DELIVERIES[:] = deliveries(200)
r, c = check_conn()
check(c["scope"]["status"] == "fail" and "1 of 3 accounts" in c["scope"]["detail"] and "Scoped" in c["scope"]["detail"], "A Scoped API key that only sees some numbers is caught and the hidden numbers are named")
VISIBLE_FOR_KEY.clear()
r, c = check_conn()
check(c["scope"]["status"] == "ok", "With a key that sees every number, that check passes")
settings.UNIPILE_SERVICE_KEY = ""
r, c = check_conn()
check("scope" not in c, "(no Service Key saved: nothing to compare with, so the check is skipped)")
httpx.post = _real_post

# --- demo-data cleanup ------------------------------------------------------------
from app.services.demo_cleanup import purge_demo_data  # noqa: E402
from app.models.user import User  # noqa: E402
from app.models.contact import Contact  # noqa: E402
from app.models.campaign import Campaign  # noqa: E402
from app.models.chatbot import Chatbot  # noqa: E402
from app.models.api_key import ApiKey  # noqa: E402
with SessionLocal() as db:
    real_numbers = db.query(WhatsAppNumber).filter(WhatsAppNumber.unipile_account_id.isnot(None)).count()
    real_convs = db.query(Conversation).join(WhatsAppNumber, WhatsAppNumber.id == Conversation.whatsapp_number_id).filter(WhatsAppNumber.unipile_account_id.isnot(None)).count()
    real_msgs = db.query(Message).join(Conversation, Conversation.id == Message.conversation_id).join(WhatsAppNumber, WhatsAppNumber.id == Conversation.whatsapp_number_id).filter(WhatsAppNumber.unipile_account_id.isnot(None)).count()
    users_before, numbers_before = db.query(User).count(), db.query(WhatsAppNumber).count()
    # a real-looking number someone added by hand AFTER the seed must survive (backdate the seed-like timestamps)
    from datetime import timedelta as _td  # noqa: E402
    manual = WhatsAppNumber(phone="+15550001111", display_name="Added by hand", admin_id=db.query(User).filter(User.email == "rahul.sharma@relaycrm.io").first().id)
    db.add(manual); db.commit()
    manual.created_at = _dt.now(_tz.utc) + _td(days=2); db.commit()
    numbers_before += 1
    dry = purge_demo_data(db, dry_run=True)
    check(dry["error"] is None and dry["new_owner"] == "boss@example.com" and dry["deleted"]["numbers"] == numbers_before - real_numbers - 1 and dry["deleted"]["users"] == 13,
          "Dry run lists the demo data (not the number added by hand) and who inherits the real numbers")
    boss_row = db.query(User).filter(User.email == "boss@example.com").first()
    boss_row.role = "admin" if isinstance(boss_row.role, str) else type(boss_row.role).ADMIN; db.commit()
    nolog = purge_demo_data(db, dry_run=True)
    check(nolog["new_owner"] is None and nolog["deleted"]["users"] == 0 and nolog["deleted"]["numbers"] == numbers_before - real_numbers - 1 and "demo logins" in nolog["warning"],
          "Without a real Super Admin the demo data goes but the logins stay (no lock-out)")
    boss_row.role = type(boss_row.role).SUPER_ADMIN; db.commit()
    check(db.query(User).count() == users_before and db.query(WhatsAppNumber).count() == numbers_before, "A dry run changes nothing")
    check(purge_demo_data(db, keep_emails=["rahul.sharma@relaycrm.io"], dry_run=True)["deleted"]["users"] == 12, "A listed account can be kept")
    rep = purge_demo_data(db, dry_run=False)
    check(rep["error"] is None, "Cleanup runs")
with SessionLocal() as db:
    check([u.email for u in db.query(User).all()] == ["boss@example.com"], "Only the real account is left")
    check(db.query(WhatsAppNumber).count() == real_numbers + 1 and db.query(WhatsAppNumber).filter(WhatsAppNumber.unipile_account_id.is_(None)).count() == 1
          and db.query(WhatsAppNumber).filter(WhatsAppNumber.phone == "+15550001111").count() == 1, "Fake numbers gone; connected numbers and the one added by hand kept")
    check(db.query(Conversation).count() == real_convs and db.query(Message).count() == real_msgs, "Real chats and messages are untouched")
    boss_id = db.query(User).first().id
    check(db.query(Contact).filter(Contact.admin_id != boss_id).count() == 0 and db.query(Conversation).filter(Conversation.admin_id != boss_id).count() == 0
          and db.query(WhatsAppNumber).filter(WhatsAppNumber.admin_id != boss_id).count() == 0, "What demo users owned now belongs to the real account")
    check(db.query(Campaign).count() == 0 and db.query(Chatbot).count() == 0 and db.query(ApiKey).count() == 0, "Demo campaigns, chatbots and keys are gone")
check(client.post("/api/v1/auth/login", json={"email": "rahul.sharma@relaycrm.io", "password": "password123"}).status_code == 401
      and client.post("/api/v1/auth/login", json={"email": "deepa@relaycrm.io", "password": "password123"}).status_code == 401, "Demo logins no longer work")
check(client.post("/api/v1/auth/login", json={"email": "boss@example.com", "password": "newpass-2222"}).status_code == 200, "The real login still works")
tok2 = client.post("/api/v1/auth/login", json={"email": "boss@example.com", "password": "newpass-2222"}).json()["access_token"]
check(len(client.get("/api/v1/numbers", headers={"Authorization": f"Bearer {tok2}"}).json()) == real_numbers + 1
      and client.get("/api/v1/analytics/summary", headers={"Authorization": f"Bearer {tok2}"}).json()["messages_sent"] > 0, "The dashboard now shows only real numbers and figures")
with SessionLocal() as db:
    check(purge_demo_data(db, dry_run=False).get("note", "").startswith("No demo"), "Running the cleanup again is harmless")

httpx.get = httpx_get
print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
