from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    PROJECT_NAME: str = "Relay CRM API"
    API_V1_PREFIX: str = "/api/v1"

    DATABASE_URL: str = "postgresql://relay_user:relay_pass@localhost:5432/relay_crm"

    SECRET_KEY: str = "dev-secret-change-me"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440

    # Live by default: messages really go out and real chats are imported.
    # Only set true on a developer machine (fake sends, no network calls).
    WHATSAPP_MOCK: bool = False

    # Unipile v2. UNIPILE_API_KEY: a Service Key (it also manages webhooks) from
    # the Unipile dashboard > API Keys, sent as X-API-KEY. Keys expire — rotate
    # before the expiry date. UNIPILE_WEBHOOK_SECRET: signing secret of the webhook
    # endpoint you create there. Both are passwords — environment only.
    UNIPILE_API_KEY: str = ""
    UNIPILE_BASE_URL: str = "https://api.unipile.com/v2"
    UNIPILE_WEBHOOK_SECRET: str = ""

    # Public https URL of this backend (e.g. https://relay-backend.onrender.com).
    # Used to show the webhook address in the app.
    PUBLIC_BASE_URL: str = ""

    # Seconds between campaign messages. These are normal WhatsApp accounts, not
    # the official Business API — keep this at 5+ to protect numbers from bans.
    CAMPAIGN_SEND_INTERVAL_SECONDS: float = 5.0

    # One-time cleanup of the demo seed (see app/services/demo_cleanup.py): set true,
    # deploy once, then remove it. PURGE_KEEP_EMAILS = comma list of demo-domain
    # accounts to keep.
    PURGE_DEMO_DATA: bool = False
    PURGE_KEEP_EMAILS: str = ""

    # Optional first Super Admin, created on startup if that email doesn't exist
    # yet (so production never needs the demo seed). Remove the password
    # variable once you've logged in and changed it if you like.
    BOOTSTRAP_ADMIN_EMAIL: str = ""
    BOOTSTRAP_ADMIN_PASSWORD: str = ""
    BOOTSTRAP_ADMIN_NAME: str = "Super Admin"

    # Google Sign-In. Empty = the "Sign in with Google" button is hidden.
    # Only the OAuth *Client ID* is needed (no client secret for this flow).
    GOOGLE_CLIENT_ID: str = ""

    # Wide open for local development so the frontend can be served from
    # any origin (a Vite/CRA dev server, or a sandboxed artifact preview)
    # and still reach this API on localhost. Lock this down to your actual
    # frontend origin(s) before deploying anywhere reachable from the
    # internet — CORS is not a substitute for auth, but an open CORS policy
    # on a public deployment needlessly widens the attack surface.
    CORS_ORIGINS: list[str] = ["*"]


settings = Settings()
