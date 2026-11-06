"""
End-to-end tests for the Twilio WhatsApp integration — no Twilio account or
network needed. A fake Twilio client records every API call, and webhooks are
signed with Twilio's real signature algorithm.

    python test_twilio.py
"""
import os
import sys
import tempfile
import time
import types

db_file = os.path.join(tempfile.mkdtemp(), "twilio_test.db")
os.environ.update(
    DATABASE_URL=f"sqlite:///{db_file}", SECRET_KEY="test", WHATSAPP_MOCK="false", WHATSAPP_PROVIDER="twilio",
    TWILIO_ACCOUNT_SID="AC" + "0" * 32, TWILIO_AUTH_TOKEN="test-auth-token",
    PUBLIC_BASE_URL="https://crm.example.com", CAMPAIGN_SEND_INTERVAL_SECONDS="0",
)

import run_dev_sqlite  # noqa: E402  (SQLite type shims)
import sqlalchemy.dialects.postgresql as pg  # noqa: E402
pg.UUID = lambda as_uuid=False, *a, **k: run_dev_sqlite._SqliteUUID()
pg.ARRAY = lambda item_type=None, *a, **k: run_dev_sqlite._SqliteARRAY(item_type)

from fastapi.testclient import TestClient  # noqa: E402
from twilio.base.exceptions import TwilioRestException  # noqa: E402
from twilio.request_validator import RequestValidator  # noqa: E402

import app.seed as seed  # noqa: E402
import app.services.whatsapp_service as ws  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.main import app  # noqa: E402


class FakeMessages:
    def __init__(self):
        self.calls = []
        self.fail_to = set()  # E.164 numbers that should fail like Twilio would

    def create(self, **kw):
        self.calls.append(kw)
        if kw["to"].replace("whatsapp:", "") in self.fail_to:
            raise TwilioRestException(400, "/Messages", msg="Invalid WhatsApp destination", code=63003)
        return types.SimpleNamespace(sid=f"SM{len(self.calls):032d}", status="queued")


fake = types.SimpleNamespace(messages=FakeMessages())
ws._twilio_singleton = ws.TwilioWhatsAppService(client=fake)

seed.run()
client = TestClient(app)
validator = RequestValidator(settings.TWILIO_AUTH_TOKEN)
passed = failed = 0


def check(cond, label):
    global passed, failed
    passed, failed = passed + bool(cond), failed + (not cond)
    print(("[OK] " if cond else "[FAIL] ") + label)


def login(email):
    t = client.post("/api/v1/auth/login", json={"email": email, "password": "password123"}).json()["access_token"]
    return {"Authorization": f"Bearer {t}"}


def webhook(path, params, sign=True):
    url = f"https://crm.example.com/api/v1/webhooks/twilio/{path}"
    headers = {"X-Twilio-Signature": validator.compute_signature(url, params) if sign else "bogus"}
    return client.post(f"/api/v1/webhooks/twilio/{path}", data=params, headers=headers)


SA = login("deepa@relaycrm.io")
ADMIN = login("rahul.sharma@relaycrm.io")
rahul_id = client.get("/api/v1/admins/me", headers=ADMIN).json()["id"]

# --- setup: the sandbox number as a CRM WhatsApp Number owned by Rahul --------
sandbox = client.post("/api/v1/numbers", headers=SA, json={"phone": "+1 415 523 8886", "display_name": "Twilio Sandbox", "admin_id": rahul_id}).json()
check(sandbox.get("phone") == "+1 415 523 8886", "Sandbox number added as a CRM WhatsApp Number")
status = client.get("/api/v1/system/whatsapp", headers=ADMIN).json()
check(status["live"] and status["provider"] == "twilio" and status["inbound_webhook_url"].endswith("/webhooks/twilio/inbound"),
      "/system/whatsapp reports Twilio live + webhook URLs")
check(client.get("/health").json()["whatsapp_provider"] == "twilio", "/health shows provider=twilio")

# --- inbound: a brand-new customer writes to the sandbox ----------------------
r = webhook("inbound", {"MessageSid": "SMin001", "From": "whatsapp:+919876500001", "To": "whatsapp:+14155238886",
                        "Body": "Hi, is the sale still on?", "ProfileName": "Asha Rao", "NumMedia": "0"})
check(r.status_code == 200 and "<Response>" in r.text, "Inbound webhook accepted, returns empty TwiML")
convs = client.get("/api/v1/inbox/conversations", headers=ADMIN).json()
asha_conv = next((c for c in convs if c["contact"]["phone"] == "+919876500001"), None)
check(asha_conv and asha_conv["contact"]["name"] == "Asha Rao", "Unknown sender becomes a new contact (WhatsApp profile name)")
check(asha_conv and asha_conv["unread_count"] == 1 and asha_conv["status"] == "needs_human" and asha_conv["last_inbound_at"],
      "Conversation created: unread=1, needs_human, 24h window open")
check(webhook("inbound", {"MessageSid": "SMin002", "From": "whatsapp:+919876500001", "To": "whatsapp:+14155238886", "Body": "x"}, sign=False).status_code == 403,
      "Webhook with bad signature is rejected (403)")
webhook("inbound", {"MessageSid": "SMin001", "From": "whatsapp:+919876500001", "To": "whatsapp:+14155238886", "Body": "Hi, is the sale still on?"})
detail = client.get(f"/api/v1/inbox/conversations/{asha_conv['id']}", headers=ADMIN).json()
check(sum(m["text"] == "Hi, is the sale still on?" for m in detail["messages"]) == 1, "Twilio retries of the same message are de-duplicated")

# --- inbox replies go out through Twilio from the right number -----------------
r = client.post(f"/api/v1/inbox/conversations/{asha_conv['id']}/messages", headers=ADMIN, json={"type": "text", "text": "Yes! 20% off till Sunday"})
call = fake.messages.calls[-1]
check(r.status_code == 200 and call["from_"] == "whatsapp:+14155238886" and call["to"] == "whatsapp:+919876500001" and call["body"] == "Yes! 20% off till Sunday",
      "Inbox reply sent via Twilio from the conversation's number to the customer")
check(call.get("status_callback") == "https://crm.example.com/api/v1/webhooks/twilio/status", "Status callback URL attached to every send")
reply = r.json()
check(reply["status"] == "sent", "Reply stored as sent")

r = client.post(f"/api/v1/inbox/conversations/{asha_conv['id']}/messages", headers=ADMIN,
                json={"type": "image", "media_url": "https://example.com/offer.jpg", "text": "Our offer"})
call = fake.messages.calls[-1]
check(call.get("media_url") == ["https://example.com/offer.jpg"] and call.get("body") == "Our offer", "Image sent as Twilio media with caption")
r = client.post(f"/api/v1/inbox/conversations/{asha_conv['id']}/messages", headers=ADMIN,
                json={"type": "text", "content_sid": "HX" + "a" * 32, "content_variables": {"1": "Asha"}})
call = fake.messages.calls[-1]
check(call.get("content_sid") == "HX" + "a" * 32 and call.get("content_variables") == '{"1": "Asha"}', "Template send uses content_sid + content_variables")

# --- delivery receipts ---------------------------------------------------------
sid = fake_sid = None
detail = client.get(f"/api/v1/inbox/conversations/{asha_conv['id']}", headers=ADMIN).json()
first_reply = next(m for m in detail["messages"] if m["text"] == "Yes! 20% off till Sunday")
from app.db.session import SessionLocal  # noqa: E402
from app.models.conversation import Message  # noqa: E402
with SessionLocal() as db:
    sid = db.get(Message, __import__("uuid").UUID(first_reply["id"])).provider_message_id
check(bool(sid), "Twilio message SID saved on the message")
check(webhook("status", {"MessageSid": sid, "MessageStatus": "delivered"}).status_code == 204, "Status webhook accepted")
webhook("status", {"MessageSid": sid, "MessageStatus": "read"})
webhook("status", {"MessageSid": sid, "MessageStatus": "sent"})  # late, out-of-order
detail = client.get(f"/api/v1/inbox/conversations/{asha_conv['id']}", headers=ADMIN).json()
check(next(m for m in detail["messages"] if m["id"] == first_reply["id"])["status"] == "read", "Delivered -> read, and a late 'sent' doesn't move it backwards")

# --- failures are recorded, not crashes ---------------------------------------
fake.messages.fail_to.add("+919876500001")
r = client.post(f"/api/v1/inbox/conversations/{asha_conv['id']}/messages", headers=ADMIN, json={"type": "text", "text": "will fail"})
check(r.status_code == 200 and r.json()["status"] == "failed" and r.json()["error_code"] == "63003", "Twilio error -> message marked failed with error code 63003")
fake.messages.fail_to.clear()

# --- STOP opt-out --------------------------------------------------------------
webhook("inbound", {"MessageSid": "SMin003", "From": "whatsapp:+919876500001", "To": "whatsapp:+14155238886", "Body": "STOP"})
r = client.post(f"/api/v1/inbox/conversations/{asha_conv['id']}/messages", headers=ADMIN, json={"type": "text", "text": "hello?"})
check(r.status_code == 400 and "opted out" in r.json()["detail"], "After STOP, sending to that contact is blocked")
webhook("inbound", {"MessageSid": "SMin004", "From": "whatsapp:+919876500001", "To": "whatsapp:+14155238886", "Body": "START"})
r = client.post(f"/api/v1/inbox/conversations/{asha_conv['id']}/messages", headers=ADMIN, json={"type": "text", "text": "welcome back"})
check(r.status_code == 200, "START opts them back in")

# --- Click-to-WhatsApp ad lead --------------------------------------------------
webhook("inbound", {"MessageSid": "SMad001", "From": "whatsapp:+919876500002", "To": "whatsapp:+14155238886", "Body": "I saw your ad",
                    "ProfileName": "Vikas", "ReferralHeadline": "Diwali Mega Sale", "ReferralSourceUrl": "https://fb.me/xyz", "ReferralCtwaClid": "abc"})
contacts = client.get("/api/v1/contacts", headers=ADMIN, params={"search": "+919876500002"}).json()["items"]
check(contacts and contacts[0]["source"] == "Click-to-WhatsApp ad: Diwali Mega Sale" and "From ad" in contacts[0]["tags"],
      "Ad lead: contact tagged 'From ad' with the ad headline as source")

# --- unknown destination number ------------------------------------------------
r = webhook("inbound", {"MessageSid": "SMx", "From": "whatsapp:+919876500009", "To": "whatsapp:+15550000000", "Body": "?"})
check(r.status_code == 200, "Message to a number not in the CRM is ignored safely (logged)")

# --- campaigns -----------------------------------------------------------------
# Rahul's contacts: tag two of the new ones "VIP"
for c in client.get("/api/v1/contacts", headers=ADMIN, params={"per_page": 100}).json()["items"]:
    if c["phone"] in ("+919876500001", "+919876500002"):
        client.post(f"/api/v1/contacts/{c['id']}/tags", headers=ADMIN, json={"tag": "VIP"})
prev = client.post("/api/v1/campaigns/audience-preview", headers=ADMIN, json={"whatsapp_number_id": sandbox["id"], "audience_filter": {"tag": "vip"}}).json()
check(prev["recipients"] >= 2, f"Audience preview by tag (case-insensitive): {prev['recipients']} recipients")
all_prev = client.post("/api/v1/campaigns/audience-preview", headers=ADMIN, json={"whatsapp_number_id": sandbox["id"], "audience_filter": {}}).json()

# opt one VIP out so it must be skipped
webhook("inbound", {"MessageSid": "SMin005", "From": "whatsapp:+919876500002", "To": "whatsapp:+14155238886", "Body": "stop"})
prev2 = client.post("/api/v1/campaigns/audience-preview", headers=ADMIN, json={"whatsapp_number_id": sandbox["id"], "audience_filter": {"tag": "VIP"}}).json()
check(prev2["recipients"] == prev["recipients"] - 1 and prev2["opted_out_skipped"] >= 1, "Opted-out contacts are excluded from the audience")

camp = client.post("/api/v1/campaigns", headers=ADMIN, json={"name": "VIP Diwali", "whatsapp_number_id": sandbox["id"],
                    "audience_filter": {"tag": "VIP"}, "message_text": "Hi {{name}}, VIP early access is live!"}).json()
before = len(fake.messages.calls)
r = client.post(f"/api/v1/campaigns/{camp['id']}/launch", headers=ADMIN)
check(r.status_code == 200 and r.json()["recipients"] == prev2["recipients"], "Launch sets recipients from the audience")
for _ in range(50):
    c = next(x for x in client.get("/api/v1/campaigns", headers=ADMIN).json() if x["id"] == camp["id"])
    if c["status"] != "Running":
        break
    time.sleep(0.1)
sent_calls = fake.messages.calls[before:]
check(c["status"] == "Completed" and c["sent"] == prev2["recipients"] and c["finished_at"], f"Campaign completed: {c['sent']} sent")
check(all(x["to"] != "whatsapp:+919876500002" for x in sent_calls), "Opted-out contact received nothing")
check(any(x["body"] == "Hi Asha, VIP early access is live!" for x in sent_calls), "{{name}} personalised per contact")
asha_detail = client.get(f"/api/v1/inbox/conversations/{asha_conv['id']}", headers=ADMIN).json()
camp_msg = next((m for m in asha_detail["messages"] if m["campaign_id"] == camp["id"]), None)
check(camp_msg is not None, "Campaign messages appear in each contact's inbox thread")

# receipts + replies count toward the campaign
with SessionLocal() as db:
    csid = db.get(Message, __import__("uuid").UUID(camp_msg["id"])).provider_message_id
webhook("status", {"MessageSid": csid, "MessageStatus": "delivered"})
webhook("status", {"MessageSid": csid, "MessageStatus": "read"})
webhook("inbound", {"MessageSid": "SMin006", "From": "whatsapp:+919876500001", "To": "whatsapp:+14155238886", "Body": "Interested!"})
c = next(x for x in client.get("/api/v1/campaigns", headers=ADMIN).json() if x["id"] == camp["id"])
check(c["delivered"] == 1 and c["read"] == 1 and c["replies"] == 1, f"Campaign counts delivered / read / replies from webhooks ({c['delivered']}/{c['read']}/{c['replies']})")

# failure mid-campaign + pause/resume without duplicates
settings.CAMPAIGN_SEND_INTERVAL_SECONDS = 0.15
fake.messages.fail_to.add("+919876500001")
camp2 = client.post("/api/v1/campaigns", headers=ADMIN, json={"name": "Everyone", "whatsapp_number_id": sandbox["id"], "message_text": "Hello {{name}}"}).json()
before = len(fake.messages.calls)
client.post(f"/api/v1/campaigns/{camp2['id']}/launch", headers=ADMIN)
time.sleep(0.5)
client.patch(f"/api/v1/campaigns/{camp2['id']}", headers=ADMIN, json={"status": "Paused"})
time.sleep(0.4)
paused_count = len(fake.messages.calls) - before
c2 = next(x for x in client.get("/api/v1/campaigns", headers=ADMIN).json() if x["id"] == camp2["id"])
check(c2["status"] == "Paused" and 0 < paused_count < c2["recipients"], f"Pause stops sending ({paused_count}/{c2['recipients']} sent)")
settings.CAMPAIGN_SEND_INTERVAL_SECONDS = 0
client.post(f"/api/v1/campaigns/{camp2['id']}/launch", headers=ADMIN)
for _ in range(50):
    c2 = next(x for x in client.get("/api/v1/campaigns", headers=ADMIN).json() if x["id"] == camp2["id"])
    if c2["status"] != "Running":
        break
    time.sleep(0.1)
tos = [x["to"] for x in fake.messages.calls[before:]]
check(c2["status"] == "Completed" and len(tos) == len(set(tos)) == c2["recipients"], "Resume finishes the rest with no duplicate sends")
check(c2["failed"] >= 1 and c2["sent"] + c2["failed"] == c2["recipients"], "Failed recipients counted separately")
fake.messages.fail_to.clear()

# --- bulk import + one-click campaign to a pasted list --------------------------
rows = [{"phone": "98200 11234", "name": "Ravi"}, {"phone": "+91 98200 11234"},  # same number twice
        {"phone": "098765 43210", "name": "Sita"}, {"phone": "12345"}, {"phone": "58200 11234"},
        {"phone": "+919876500001", "name": "Asha R"}]  # already a contact
r = client.post("/api/v1/contacts/import", headers=ADMIN, json={"whatsapp_number_id": sandbox["id"], "rows": rows, "tag": "Diwali list", "consent": False})
check(r.status_code == 400, "Import refused without opt-in confirmation")
r = client.post("/api/v1/contacts/import", headers=ADMIN, json={"whatsapp_number_id": sandbox["id"], "rows": rows, "tag": "Diwali list", "consent": True}).json()
check(r["created"] == 2 and r["updated"] == 1 and r["duplicates_in_file"] == 1 and len(r["invalid"]) == 2,
      f"Import: 2 new, 1 existing updated, 1 duplicate, 2 invalid ({r['created']}/{r['updated']}/{r['duplicates_in_file']}/{len(r['invalid'])})")
check({e["reason"] for e in r["invalid"]} == {"wrong length", "not an Indian mobile number"}, "Invalid rows explained")
ravi = client.get("/api/v1/contacts", headers=ADMIN, params={"search": "+919820011234"}).json()["items"]
check(ravi and ravi[0]["name"] == "Ravi" and "Diwali list" in ravi[0]["tags"], "10-digit number saved as +91..., tagged with the list name")
r2 = client.post("/api/v1/contacts/import", headers=ADMIN, json={"whatsapp_number_id": sandbox["id"], "rows": rows, "tag": "Diwali list", "consent": True}).json()
check(r2["created"] == 0 and r2["updated"] == 3, "Re-importing the same list creates no duplicates")
foreign_num = client.get("/api/v1/numbers", headers=SA).json()
foreign_num = next(n for n in foreign_num if n["admin_id"] != rahul_id)
check(client.post("/api/v1/contacts/import", headers=ADMIN, json={"whatsapp_number_id": foreign_num["id"], "rows": rows, "consent": True}).status_code == 404,
      "Admin can't import into another admin's number")

prev = client.post("/api/v1/campaigns/audience-preview", headers=ADMIN, json={"whatsapp_number_id": sandbox["id"], "audience_filter": {"tag": "Diwali list"}}).json()
check(prev["recipients"] == 3 and prev["opted_out_skipped"] == 0, "List audience = the 3 valid unique numbers (Asha re-subscribed with START)")
camp3 = client.post("/api/v1/campaigns", headers=ADMIN, json={"name": "Diwali list blast", "whatsapp_number_id": sandbox["id"], "audience_filter": {"tag": "Diwali list"},
                     "content_sid": "HX" + "c" * 32, "content_variables": {"1": "{{name}}", "2": "Sunday"}}).json()
before = len(fake.messages.calls)
client.post(f"/api/v1/campaigns/{camp3['id']}/launch", headers=ADMIN)
for _ in range(50):
    c3 = next(x for x in client.get("/api/v1/campaigns", headers=ADMIN).json() if x["id"] == camp3["id"])
    if c3["status"] != "Running":
        break
    time.sleep(0.1)
sent3 = fake.messages.calls[before:]
check(c3["status"] == "Completed" and sorted(x["to"] for x in sent3) == ["whatsapp:+919820011234", "whatsapp:+919876500001", "whatsapp:+919876543210"],
      "One-click campaign reached exactly the imported numbers")
check(any(x.get("content_variables") == '{"1": "Ravi", "2": "Sunday"}' for x in sent3), "Template variable {{name}} personalised per contact")

check(client.post("/api/v1/campaigns", headers=ADMIN, json={"name": "empty", "whatsapp_number_id": sandbox["id"]}).status_code == 200
      and client.post(f"/api/v1/campaigns/{client.get('/api/v1/campaigns', headers=ADMIN).json()[0]['id']}/launch", headers=ADMIN).status_code == 400,
      "Launching a campaign with no message is refused")

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
