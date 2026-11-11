# Relay CRM — Backend

FastAPI + SQLAlchemy + PostgreSQL backend for the WhatsApp CRM platform.
Mirrors the frontend's mock data and service layer exactly, so swapping the
frontend from mock services to real HTTP calls against this API is a
find-and-replace job, not a redesign.

## Stack

- **FastAPI** — routing, validation, OpenAPI docs at `/docs`
- **SQLAlchemy 2.0** — ORM, models in `app/models/`
- **PostgreSQL** — primary datastore
- **JWT (python-jose)** — auth; role claim (`admin` / `super_admin`) embedded in the token
- **Passlib/bcrypt** — password hashing

## Status: integration-tested against the real frontend calls

This has been tested two ways:
1. **Backend alone** — an end-to-end smoke test (auth, role-scoping, inbox
   send, campaign create→launch, analytics, API-key masking) run against
   this exact codebase with SQLite substituted for Postgres, since Postgres
   wasn't installable in the build sandbox.
2. **Frontend integration** — `integration_test.mjs` (included in this
   folder) replays the *exact* fetch calls the React frontend makes on every
   page mount and every action (add number, send inbox message, launch
   campaign, toggle automation, add/test/remove API key, etc.) against a
   live running instance of this server, and checks the response shapes
   match what the frontend's JSX actually reads field-by-field. **37/37
   checks passed**, including a fix this testing caught: conversations
   originally returned bare IDs with no contact name/phone, which the
   inbox UI needs — `ConversationOut` now embeds a `contact` object and
   `whatsapp_number_phone` specifically because this integration pass
   surfaced the mismatch.

Also verified against **real PostgreSQL 16** (seed + 37/37 integration
checks + the regression checks in `integration_test.mjs`).

Run it yourself:
```bash
python run_dev_sqlite.py        # quick local server on :8000, no Postgres needed
                                # (--port 8001 for another port, --reset to reseed)
# in another terminal:
node integration_test.mjs       # replays real frontend calls, prints pass/fail
```


## Setup

```bash
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env
# edit .env — set DATABASE_URL to your local Postgres, and SECRET_KEY to
# something random (e.g. `python -c "import secrets; print(secrets.token_hex(32))"`)
```

Create the database (adjust to your local Postgres setup):
```bash
createdb relay_crm
# or: psql -c "CREATE DATABASE relay_crm;"
```

## First run

```bash
python -m app.seed        # creates tables + populates mock data
uvicorn app.main:app --reload --port 8000
```

Open `http://localhost:8000/docs` for interactive API docs.

**Seeded logins** (printed by the seed script too):
- Super Admin: `deepa@relaycrm.io` / `password123`
- Admin: `rahul.sharma@relaycrm.io` / `password123` (and 11 more admins — see seed output)

## Auth flow

```
POST /api/v1/auth/login  {email, password}  →  {access_token, role, ...}
```
Send `Authorization: Bearer <token>` on every other request. The token's
`role` claim drives access control — Super-Admin-only routes (`/admins`,
`/api-keys`, `/activity-logs`) reject Admin tokens with 403; every other
route auto-scopes to `current_user.id` when the caller is an Admin.

## Multi-tenancy model

Single database, row-level `admin_id` ownership — matches the frontend's
data model exactly. `app/api/deps.py::admin_scope_filter` is the one place
this is enforced: routers depend on it and filter every query by it, rather
than trusting an `admin_id` the client might send. Revisit this file first
if per-tenant isolation is ever needed.

## Where each frontend service maps

| Frontend mock service | Backend router | File |
|---|---|---|
| `adminService` | `/admins` | `app/api/v1/endpoints/admins.py` |
| `whatsappService` | `/numbers` | `app/api/v1/endpoints/numbers.py` |
| `contactService` | `/contacts` | `app/api/v1/endpoints/contacts.py` |
| Inbox | `/inbox` | `app/api/v1/endpoints/inbox.py` |
| `campaignService` | `/campaigns` | `app/api/v1/endpoints/campaigns.py` |
| `automationService` | `/automations` | `app/api/v1/endpoints/automations.py` |
| `chatbotService` | `/chatbots` | `app/api/v1/endpoints/chatbot.py` |
| `analyticsService` | `/analytics` | `app/api/v1/endpoints/analytics.py` |
| `apiKeyService` | `/api-keys` | `app/api/v1/endpoints/api_keys.py` |
| Activity Logs | `/activity-logs` | `app/api/v1/endpoints/activity_logs.py` |

## WhatsApp integration — Unipile only

Every message-sending code path (inbox replies, campaign dispatch, welcome
automation) calls `app.services.whatsapp_service.get_whatsapp_service()`.
`WHATSAPP_MOCK=true` fakes sends for local development; `WHATSAPP_MOCK=false`
sends through [Unipile v2](https://developer.unipile.com/v2.0/docs/) — WhatsApp
accounts linked by QR / pairing code. There are no other providers.
Code: `UnipileWhatsAppService` in `whatsapp_service.py`, routes in
`app/api/v1/endpoints/unipile.py`.

1. Unipile dashboard → your Application → API Keys → **Service Keys**: create one →
   `UNIPILE_API_KEY` (shown once; keys expire, rotate before the date; sent as `X-API-KEY`).
2. Dashboard → Webhooks → add `{PUBLIC_BASE_URL}/api/v1/whatsapp/webhook` (the old
   `/api/v1/unipile/webhook` keeps working) with events `message.new`,
   `message.receipt.delivery`, `message.receipt.read`, `account.status.*`, `account.remove`;
   copy its signing secret → `UNIPILE_WEBHOOK_SECRET`.
3. Set `PUBLIC_BASE_URL`, `WHATSAPP_MOCK=false`.
4. Nothing is linked from the app: connect WhatsApp numbers in the Unipile dashboard.
   `app/services/sync.py` imports every connected WhatsApp account as a CRM number (owned
   by the first Super Admin, who can reassign it with `PATCH /numbers/{id}`), then once per
   number its latest 100 one-to-one chats with their last 30 messages. It re-checks for new
   accounts every minute. After that, messages arrive through the webhook.
5. The Inbox is unified: all numbers' chats in one list, each tagged with its number
   (`GET /inbox/conversations`, optional `?number_id=`). Replies go out from the number the
   chat belongs to.

Limits: no templates and no 24h window (it's a normal WhatsApp account — pace
campaigns with `CAMPAIGN_SEND_INTERVAL_SECONDS` of 5+ to avoid bans); group chats are
not imported or shown, and senders whose phone Unipile doesn't expose (`@lid` with no `public_identifier`) are skipped
and noted in the activity log. Test without an account: `python test_unipile.py`.

### Live data only (no demo mode, no demo data)

`WHATSAPP_MOCK` defaults to `false`: messages really go out and the connected accounts and
their chats are imported. Every figure in the app is counted from the real messages
(`app/services/stats.py`): analytics totals, the per-day charts, each number's messages today /
delivery rate / reply rate, and the Admin performance table. Numbers' connection status is asked
from the WhatsApp service every 10 s; the inbox refreshes every 5 s and new messages arrive by webhook.

The demo seed (`python -m app.seed`, accounts `*@relaycrm.io` with the password `password123`) is removed
automatically at every startup (`app/services/demo_cleanup.py`; developer machines set `KEEP_DEMO_DATA=true`).
Two steps, so you can't be locked out: the **data** (fake numbers and everything attached, seeded automations,
keys, logs) is always removed; the **demo logins** are removed only once a real Super Admin exists
(`BOOTSTRAP_ADMIN_EMAIL` / `BOOTSTRAP_ADMIN_PASSWORD`) and whatever they owned moves to that account.
Connected numbers and their chats are never touched. Preview with `python -m app.purge_demo`,
apply by hand with `--yes`. `PURGE_KEEP_EMAILS` lists demo-domain accounts to keep.

A configured `UNIPILE_API_KEY` always means live mode, even if `WHATSAPP_MOCK=true` is still set.
If your numbers don't appear: Super Admin → WhatsApp Numbers → **Refresh** shows the exact error
(wrong key, key not allowed to list accounts → use a Service Key or `UNIPILE_ACCOUNT_IDS`, ...).

### Message templates and webhook-driven automations

**Templates** are your own saved messages (`/templates`: name + text, `{{name}}` = the customer's first name). They
are sent as ordinary WhatsApp messages, so no provider approval is involved. Pick one from the chat box, from a
campaign's message, or into any automation message step.

**Automations are started by the incoming-message webhook.** Every `message.new` can start a run:
`new_customer` (first message from someone new) or `keyword` (the message contains one of the automation's
keywords: whole words, any capitals, at most once per chat per automation per 24 h; `trigger_config.keywords`).
`GET /automations/templates` lists ready-made flows, including keyword auto-replies and "hand over when they ask
for a person". The standard flow (welcome, wait 2 hours, replied? yes: hand over, no: follow-up) comes first.
Each message step shows its text on the flow with **Write message** and **+ Add variant**.

### How the Unipile guide's pieces are used

- **List chats / list messages** import and catch up each number's chats (`sync.py`); a chat id is used exactly as returned.
- **`message.new` webhook** reports received messages *and* messages the account owner sends (`is_sender`): received ones
  are customers' messages; sent ones (from the phone, another device, or this app) are shown as ours in real time without
  adding unread or starting automations. A message this app sent is never shown twice (matched by id, or by text within 30 s).
- **Find Chats by User** (`GET /{account}/users/{id}/chat`) finds the existing chat when there is no stored chat id
  (campaigns, first contact); a brand-new conversation is started with `number@s.whatsapp.net`.
- **Never used:** Delete a Chat / Delete a Message. Nothing in this app deletes chats at the source.
- **Real chats are never removed by the app**: only definite non-customers (WhatsApp's own account, groups, status,
  newsletters) are ever cleaned up, and never a chat anyone replied in. Super Admin -> WhatsApp Numbers -> **Re-import chats**
  reads every chat of every number again from Unipile (deletes nothing, adds no duplicates); a number that says it was
  imported but holds no chats is imported again automatically.

### Which chats are imported, and people WhatsApp hides

Only one-to-one chats with people are customers. WhatsApp's own system chat (`0@s.whatsapp.net`, which showed up as
"WhatsApp Business +0"), groups, status and newsletters are never imported, and any left behind by an older version
are removed automatically. WhatsApp can also identify a person only by a hidden id (`...@lid`) without sharing a phone
number: such a chat is still imported and shown ("Phone hidden by WhatsApp"), keyed internally as `lid:<id>`.
Replies always go to the chat's own id as Unipile returned it, so they reach hidden-number people too.
Campaigns skip those contacts (there is no number to send to); they can only be answered in their chat.

### Automated replies are never instant

Every message step of an automation waits a random 15-30 seconds before it is sent (set `AUTOMATION_REPLY_DELAY_MIN` /
`_MAX`, or per step in the builder: "Wait before sending", `replyDelay: {min, max}`; 0 and 0 = instant). The wait is
counted from the customer's message (or from the end of the previous wait), is stored with the run so it survives
restarts, and also applies to the follow-up inside a YES/NO branch and to keyword replies. A hand-over to a human
waits behind the message before it.

### Attachments and voice notes

The inbox composer sends real files: the paperclip / image / video buttons open the device's file picker
(`POST /inbox/conversations/{id}/attachments`, multipart: `file`, optional `caption`, `voice`, `duration`),
and the microphone button records in the browser. The file is sent through the WhatsApp service as a
base64 attachment (one per message; the caption is sent with it) and kept in the database (`media_files`)
so the thread can show it. Limit: `MAX_UPLOAD_MB` (default 16).

Voice notes: browsers record WebM/Opus; WhatsApp wants Ogg/Opus, so the server converts them with ffmpeg
(`imageio-ffmpeg` brings a static build; a system `ffmpeg` is used first if present) and sends the result as a
playable voice message with its length. If no converter works, the recording is still sent as a voice note,
unconverted.

Received photos, videos, voice notes and documents are stored as references and shown through the app
(`/media/m/{message}`), which fetches them from the service and adds the API key itself, only for the
service's own address. Links are signed, so `<img>` / `<audio>` work without a login header.

### Automations on incoming chats

When a `message.new` comes from someone who is not yet a contact, every **Running** automation of that
number's admin with trigger `new_customer` starts a run (`app/services/automation_runner.py`). A poller
thread (every 5 s) executes the flow steps in order: send a message (`messageVariants`, rotating or random
per the step's strategy; `{{name}}` = first name), wait (`delay_minutes` or parsed from "Wait 2 hours"),
then the "Customer replied?" condition runs the steps tagged `YES` or `NO`. A step whose label contains
"human"/"transfer"/"assign" hands the chat to a human. Runs are stored in `automation_runs`, so waits
survive restarts; pausing the automation stops waiting runs; opted-out contacts are never messaged.

**Built-in flow.** An automation with no saved steps, and any message step with no text, uses the built-in
flow the builder shows (new customer -> welcome -> wait 2 hours -> replied? -> YES transfer to human /
NO follow-up) with built-in wording (`app/core/automation_templates.py`), so it works with zero setup.

**Templates.** `GET /automations/templates` lists ready-made automations (Welcome new customers,
Welcome + follow-up, Hand over to a human, Qualify new leads); `POST /automations/from-template` creates
one (as *Scheduled*, ready to review and activate) for the signed-in Admin or Super Admin. The Automations
page has a **New automation** button with the gallery. Chatbots (keyword menus) are not wired to incoming
messages yet.

## Deploying on Render

`render.yaml` is a Blueprint: a web service plus a Postgres database. Run **one**
instance only (campaigns are sent by an in-process worker that resumes after restarts).
Set the secret variables marked `sync: false` in the dashboard. In production use
`BOOTSTRAP_ADMIN_EMAIL` / `BOOTSTRAP_ADMIN_PASSWORD` instead of `python -m app.seed`
(the seed creates demo logins with a known password).

## CORS

`CORS_ORIGINS` defaults to `["*"]` with `allow_credentials=False` (auth is a
Bearer token, not a cookie, so this is spec-compliant and was verified with
a real cross-origin preflight during testing). Fine for local development
and for a frontend served from anywhere during testing. **Lock this down to
your actual frontend's origin(s)** before deploying anywhere reachable from
the internet.

## Frontend integration

`relaycrm-app-live.jsx` (shipped alongside this backend, not inside this
folder) calls this API directly — see `API_BASE` in that file, currently
`http://localhost:8000/api/v1`. Update it if you run the backend somewhere
other than localhost. Everything in the frontend's nav is wired to a real
endpoint **except**: the Automation/Chatbot visual canvases (status
toggling is real, node-level editing isn't — the backend stores `flow` as
JSON with no field-level edit endpoint yet), and two Analytics charts
(messages-over-time, delivery/read/reply-by-day) which are clearly labeled
"Illustrative" in the UI because they need a per-day events table this
backend doesn't have yet (only rolling today-counters exist).

## Database migrations

`app/main.py` calls `Base.metadata.create_all()` on startup for local dev
convenience — fine until you have real data. Before that happens, switch to
Alembic:
```bash
alembic init migrations
# point migrations/env.py at app.db.base.Base.metadata
alembic revision --autogenerate -m "init"
alembic upgrade head
```

## API key security

Raw provider keys are never returned by any endpoint — only `masked` (e.g.
`••••••••••••••••8F29`). The real value is encrypted with Fernet
(`app/core/crypto.py`) before storage. The Fernet key is currently derived
from `SECRET_KEY`; swap that for a dedicated key from a secrets manager
(AWS KMS / HashiCorp Vault) before handling real customer credentials.

## Project layout

```
app/
├── main.py              FastAPI app, CORS, startup
├── seed.py               populates mock data — python -m app.seed
├── core/                 config, JWT, password hashing, key encryption
├── db/                    engine/session, declarative base
├── models/                SQLAlchemy models (one file per entity)
├── schemas/                Pydantic request/response models
├── api/
│   ├── deps.py            auth + role + admin-scope dependencies
│   └── v1/endpoints/      one router file per entity
└── services/
    └── whatsapp_service.py   mock + Unipile WhatsApp integration
```
