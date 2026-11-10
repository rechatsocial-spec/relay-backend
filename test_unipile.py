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

# Link flow talks to Unipile with module-level httpx.post / httpx.get — fake those.
link_calls = []
httpx_post, httpx_get = httpx.post, httpx.get
httpx.post = lambda url, **kw: (link_calls.append((url, kw)), httpx.Response(200, json={"link": "https://auth.unipile.com/abc"}))[1]
httpx.get = lambda url, **kw: httpx.Response(200, json={"id": "acc_123", "name": "+91 98765 43210", "status": "running", "provider": "whatsapp"})

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


def webhook(event, sign=True, ts=None):
    raw = json.dumps(event).encode()
    t = str(ts or int(time.time()))
    sig = hmac.new(SECRET.encode(), f"{t}.".encode() + raw, hashlib.sha256).hexdigest()
    headers = {"unipile-signature": f"t={t},v0={sig if sign else 'bad'}", "content-type": "application/json"}
    return client.post("/api/v1/unipile/webhook", content=raw, headers=headers)


SA = login("deepa@relaycrm.io")
ADMIN = login("rahul.sharma@relaycrm.io")
rahul_id = client.get("/api/v1/admins/me", headers=ADMIN).json()["id"]

# --- bootstrap admin + removed providers -----------------------------------------
ensure_bootstrap_admin()
ensure_bootstrap_admin()  # idempotent
r = client.post("/api/v1/auth/login", json={"email": "boss@example.com", "password": "boot-pass-1"})
check(r.status_code == 200, "Bootstrap Super Admin can log in (email normalised, created once)")
check(client.post("/api/v1/webhooks/twilio/inbound", data={"x": "1"}).status_code == 404, "Twilio webhook routes are gone")
check(client.get("/health").json() == {"status": "ok", "whatsapp_mock": False, "whatsapp_provider": "unipile"}, "/health reports Unipile")

# --- linking ------------------------------------------------------------------
check(client.post("/api/v1/unipile/link", headers=ADMIN, json={"admin_id": rahul_id}).status_code == 403, "Only a Super Admin can start linking")
r = client.post("/api/v1/unipile/link", headers=SA, json={"admin_id": rahul_id, "display_name": "Unipile Line"})
body = link_calls[-1][1]["json"]
check(r.status_code == 200 and r.json()["link"] == "https://auth.unipile.com/abc", "Link endpoint returns the Hosted Auth link")
check(link_calls[-1][0].endswith("/auth/link") and link_calls[-1][1]["headers"]["X-API-KEY"] == "key_test", "Auth link requested with X-API-KEY")
check(body["providers"] == "whatsapp" and body["redirect_uri"] == "https://crm.example.com/api/v1/unipile/callback", "Link request targets WhatsApp and our callback")
state = body["state"]

check(client.get("/api/v1/unipile/callback", params={"account_id": "acc_123", "state": "forged"}).status_code == 400, "Callback rejects a forged state")
r = client.get("/api/v1/unipile/callback", params={"account_id": "acc_123", "state": state})
check(r.status_code == 200 and r.json()["ok"], "Callback accepts the signed state")
numbers = client.get("/api/v1/numbers", headers=SA).json()
num = next((n for n in numbers if n["unipile_account_id"] == "acc_123"), None)
check(num and num["phone"] == "+919876543210" and num["display_name"] == "Unipile Line" and num["admin_id"] == rahul_id,
      "Callback creates the CRM number (phone from account name, owner from state)")
r = client.post("/api/v1/unipile/accounts", headers=SA, json={"account_id": "acc_123", "admin_id": rahul_id})
check(r.status_code == 200 and r.json()["unipile_account_id"] == "acc_123" and len(client.get("/api/v1/numbers", headers=SA).json()) == len(numbers),
      "Registering a dashboard-linked account is idempotent (no duplicate number)")
st = client.get("/api/v1/system/whatsapp", headers=ADMIN).json()
check(st["provider"] == "unipile" and st["credentials_set"] and st["inbound_webhook_url"].endswith("/unipile/webhook"), "/system/whatsapp reports Unipile + webhook URL")

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
check(r.json()["status"] == "failed" and "templates" in (r.json().get("error_message") or ""), "Templates are reported as unsupported")

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

httpx.post, httpx.get = httpx_post, httpx_get
print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
