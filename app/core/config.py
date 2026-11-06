from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    PROJECT_NAME: str = "Relay CRM API"
    API_V1_PREFIX: str = "/api/v1"

    DATABASE_URL: str = "postgresql://relay_user:relay_pass@localhost:5432/relay_crm"

    SECRET_KEY: str = "dev-secret-change-me"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440

    # When true, WhatsAppService never calls a real provider — every send is
    # logged to ActivityLog and returned as a simulated success. Flip this
    # (and fill in META_* below) when real integration is ready; no other
    # code changes needed since routers only ever talk to the service interface.
    WHATSAPP_MOCK: bool = True
    WHATSAPP_PROVIDER: str = "mock"
    META_ACCESS_TOKEN: str = ""
    META_PHONE_NUMBER_ID: str = ""
    META_BUSINESS_ACCOUNT_ID: str = ""

    # Twilio (WHATSAPP_PROVIDER=twilio). Account SID + Auth Token from the
    # Twilio Console home page. The Auth Token is a password — .env only.
    TWILIO_ACCOUNT_SID: str = ""
    TWILIO_AUTH_TOKEN: str = ""

    # WasenderAPI (WHATSAPP_PROVIDER=wasender). Session API key from
    # wasenderapi.com dashboard -> your WhatsApp session. It's a password.
    WASENDER_API_KEY: str = ""
    WASENDER_BASE_URL: str = "https://www.wasenderapi.com/api"

    # Public https URL where Twilio can reach this backend (e.g. your Render
    # URL or a cloudflared/ngrok tunnel). Needed for incoming messages and
    # delivered/read ticks; sending works without it.
    PUBLIC_BASE_URL: str = ""

    # Campaign sending pace. The Twilio sandbox allows ~1 msg / 3 s; a real
    # registered sender allows far more (80/s) — lower this to e.g. 0.05 then.
    CAMPAIGN_SEND_INTERVAL_SECONDS: float = 3.0

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
