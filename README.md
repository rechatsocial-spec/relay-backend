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

## WhatsApp integration — currently mocked, on purpose

Every message-sending code path (inbox replies, campaign dispatch, welcome
automation) calls `app.services.whatsapp_service.get_whatsapp_service()`,
never a provider SDK directly. Right now `WHATSAPP_MOCK=true` in `.env`
routes every call to `MockWhatsAppService`, which fakes a successful send
and returns a mock message ID — no network call happens.

**To go live later:**
1. Fill in `MetaCloudWhatsAppService` in `app/services/whatsapp_service.py`
   (each method currently raises `NotImplementedError` with a pointer to the
   Meta Graph API endpoint to call).
2. Set `META_ACCESS_TOKEN`, `META_PHONE_NUMBER_ID`, `META_BUSINESS_ACCOUNT_ID`
   in `.env`.
3. Flip `WHATSAPP_MOCK=false`.

No router or model changes needed — they only ever talk to the interface.

**Not yet built, needed before real sends work:**
- A webhook receiver for delivery/read status callbacks and inbound
  messages (currently `WhatsAppNumber.messages_today` / `delivery_rate` /
  `reply_rate` are static columns a webhook handler would update).
- A background worker (Celery/RQ) for campaign dispatch — `POST
  /campaigns/{id}/launch` currently just flips status to `Running`; iterating
  the audience and actually sending (with rate limiting) is a queued job in
  production, not something to do inline in a request handler.
- Message template management/approval tracking (Meta requires
  Meta-approved templates for any business-initiated message outside the
  24-hour customer service window).

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
    └── whatsapp_service.py   mock + (stubbed) real WhatsApp integration
```
