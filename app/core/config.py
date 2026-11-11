from pydantic import field_validator, model_validator
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
    # UNIPILE_API_KEY: the key the app uses for chats, messages and sending.
    # UNIPILE_SERVICE_KEY: a separate Service Key, used only to read the webhook settings and delivery log in
    # "Check connection" (only a Service Key may manage webhooks). Optional; if it is the only key set, it is used for
    # everything. Both are passwords: environment only.
    UNIPILE_API_KEY: str = ""
    UNIPILE_SERVICE_KEY: str = ""
    UNIPILE_BASE_URL: str = "https://api.unipile.com/v2"
    UNIPILE_WEBHOOK_SECRET: str = ""
    # Only needed if the API key is not allowed to list accounts: "acc_1,acc_2,..."
    UNIPILE_ACCOUNT_IDS: str = ""

    # Public https URL of this backend (e.g. https://relay-backend.onrender.com).
    # Used to show the webhook address in the app.
    PUBLIC_BASE_URL: str = ""

    # Automated replies wait a random time (seconds) before they are sent, so they don't arrive instantly.
    # A message step can override it with `replyDelay: {min, max}`; 0 / 0 sends at once.
    AUTOMATION_REPLY_DELAY_MIN: float = 15
    AUTOMATION_REPLY_DELAY_MAX: float = 30

    # Largest file that can be sent from the inbox.
    MAX_UPLOAD_MB: int = 16

    # Seconds between campaign messages. These are normal WhatsApp accounts, not
    # the official Business API — keep this at 5+ to protect numbers from bans.
    CAMPAIGN_SEND_INTERVAL_SECONDS: float = 5.0

    # The demo seed is removed automatically at startup (app/services/demo_cleanup.py).
    # Only a developer machine that runs on the demo data sets KEEP_DEMO_DATA=true.
    # PURGE_KEEP_EMAILS = comma list of demo-domain accounts to keep.
    KEEP_DEMO_DATA: bool = False
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

    @field_validator("UNIPILE_API_KEY", "UNIPILE_SERVICE_KEY", "UNIPILE_WEBHOOK_SECRET", mode="before")
    @classmethod
    def _clean_secret(cls, v):
        # pasted values often carry a trailing space/newline or surrounding quotes
        return v.strip().strip("\"'").strip() if isinstance(v, str) else v

    @model_validator(mode="after")
    def _live_when_configured(self):
        # A real API key means real data. A stray WHATSAPP_MOCK=true must not keep a live system in demo mode.
        if not self.UNIPILE_API_KEY and self.UNIPILE_SERVICE_KEY:
            self.UNIPILE_API_KEY = self.UNIPILE_SERVICE_KEY  # a Service Key can do everything the API key can
        if self.UNIPILE_API_KEY and self.WHATSAPP_MOCK:
            self.WHATSAPP_MOCK = False
        return self

    @property
    def management_key(self) -> str:
        """The key for webhook settings: the Service Key if there is one, else the API key."""
        return self.UNIPILE_SERVICE_KEY or self.UNIPILE_API_KEY


settings = Settings()
