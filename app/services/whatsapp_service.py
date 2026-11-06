"""
Every route that sends a WhatsApp message (inbox replies, campaign dispatch)
calls get_whatsapp_service(), never a provider SDK directly.

Providers (WHATSAPP_PROVIDER in .env, only used when WHATSAPP_MOCK=false):
  - "twilio" — real sends via Twilio's WhatsApp API (implemented)
  - "meta"   — Meta Cloud API (stub, not implemented)

Every send takes `from_number` = the CRM WhatsAppNumber.phone the message goes
out from, so each admin's number sends as itself. For Twilio that number must
be a WhatsApp sender in your Twilio account (for testing: the sandbox number
+1 415 523 8886, added in the CRM as a WhatsApp Number).
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass

from app.core.config import settings

log = logging.getLogger("relay.whatsapp")


@dataclass
class SendResult:
    success: bool
    provider_message_id: str | None
    error: str | None = None
    error_code: str | None = None


def to_e164(phone: str) -> str:
    """'+91 98200 11234' / 'whatsapp:+91…' -> '+919820011234'."""
    digits = re.sub(r"\D", "", phone or "")
    return f"+{digits}" if digits else ""


class WhatsAppServiceInterface(ABC):
    name = "base"

    @abstractmethod
    def send_text(self, from_number: str, to: str, text: str) -> SendResult: ...

    @abstractmethod
    def send_media(self, from_number: str, to: str, media_url: str, caption: str | None = None) -> SendResult:
        """Image / video / voice note / sticker / document — WhatsApp decides by the file's type."""

    @abstractmethod
    def send_template(self, from_number: str, to: str, template_id: str, variables: dict | None = None) -> SendResult:
        """Pre-approved template — the only way to message someone outside the 24h window."""


class MockWhatsAppService(WhatsAppServiceInterface):
    """Simulates the provider: always 'succeeds', never makes a network call."""

    name = "mock"

    def _mock(self) -> SendResult:
        return SendResult(success=True, provider_message_id=f"mock_{uuid.uuid4().hex[:12]}")

    def send_text(self, from_number, to, text):
        return self._mock()

    def send_media(self, from_number, to, media_url, caption=None):
        return self._mock()

    def send_template(self, from_number, to, template_id, variables=None):
        return self._mock()


class TwilioWhatsAppService(WhatsAppServiceInterface):
    """
    Real sends through Twilio Programmable Messaging (WhatsApp channel).
    Docs: https://www.twilio.com/docs/whatsapp/api

    - Free-form text/media only reach people who messaged you in the last
      24 hours (sandbox: anyone who joined in the last 3 days).
    - Templates (Content SID 'HX…') work anytime once Meta approves them.
    - Delivery/read status arrives later on the status webhook.
    """

    name = "twilio"

    def __init__(self, client=None):
        if client is None:
            from twilio.rest import Client

            if not (settings.TWILIO_ACCOUNT_SID and settings.TWILIO_AUTH_TOKEN):
                raise RuntimeError("TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN must be set in .env")
            client = Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)
        self.client = client

    def _status_callback(self) -> str | None:
        base = settings.PUBLIC_BASE_URL.rstrip("/")
        return f"{base}{settings.API_V1_PREFIX}/webhooks/twilio/status" if base else None

    def _send(self, from_number: str, to: str, **kwargs) -> SendResult:
        from twilio.base.exceptions import TwilioRestException

        params = {"from_": f"whatsapp:{to_e164(from_number)}", "to": f"whatsapp:{to_e164(to)}", **kwargs}
        callback = self._status_callback()
        if callback:
            params["status_callback"] = callback
        try:
            msg = self.client.messages.create(**params)
        except TwilioRestException as e:
            log.warning("Twilio send failed (%s): %s", e.code, e.msg)
            return SendResult(False, None, error=str(e.msg)[:300], error_code=str(e.code) if e.code else None)
        except Exception as e:  # network errors, bad config
            log.exception("Twilio send crashed")
            return SendResult(False, None, error=str(e)[:300])
        return SendResult(True, msg.sid)

    def send_text(self, from_number, to, text):
        return self._send(from_number, to, body=text)

    def send_media(self, from_number, to, media_url, caption=None):
        kwargs = {"media_url": [media_url]}
        if caption:
            kwargs["body"] = caption
        return self._send(from_number, to, **kwargs)

    def send_template(self, from_number, to, template_id, variables=None):
        kwargs = {"content_sid": template_id}
        if variables:
            kwargs["content_variables"] = json.dumps({str(k): str(v) for k, v in variables.items()})
        return self._send(from_number, to, **kwargs)


class WasenderWhatsAppService(WhatsAppServiceInterface):
    """
    Sends through WasenderAPI (https://wasenderapi.com) — a normal WhatsApp
    account linked by QR code, no Meta approval and no templates.
    Docs: https://wasenderapi.com/api-docs

    - Auth: the *session* API key (Bearer) of the linked WhatsApp session.
    - The "from" number is ignored: Wasender always sends from the session's
      linked phone, so one API key = one sending number.
    - Free text goes to anyone (no 24h window / template rule), but WhatsApp can
      ban numbers that blast cold contacts. Keep campaigns paced and opted-in.
    - 429 = Wasender's own rate limit; we wait `retry_after` and retry once.
    """

    name = "wasender"

    def __init__(self, http=None):
        if http is None:
            import httpx

            if not settings.WASENDER_API_KEY:
                raise RuntimeError("WASENDER_API_KEY must be set in .env")
            http = httpx.Client(timeout=30)
        self.http = http

    def _post(self, payload: dict) -> SendResult:
        import time

        url = settings.WASENDER_BASE_URL.rstrip("/") + "/send-message"
        headers = {"Authorization": f"Bearer {settings.WASENDER_API_KEY}"}
        try:
            for attempt in (1, 2):
                r = self.http.post(url, json=payload, headers=headers)
                if r.status_code == 429 and attempt == 1:
                    try:
                        wait = float(r.json().get("retry_after") or 5)
                    except Exception:
                        wait = 5.0
                    time.sleep(min(wait, 65))
                    continue
                break
            try:
                body = r.json()
            except Exception:
                body = {}
        except Exception as e:  # network errors, bad config
            log.exception("Wasender send crashed")
            return SendResult(False, None, error=str(e)[:300])

        if r.status_code < 300 and body.get("success", True):
            data = body.get("data") or {}
            msg_id = data.get("msgId") or data.get("id") or data.get("key")
            return SendResult(True, str(msg_id) if msg_id else f"wasender_{uuid.uuid4().hex[:12]}")
        err = body.get("message") or body.get("error") or r.text or f"HTTP {r.status_code}"
        log.warning("Wasender send failed (%s): %s", r.status_code, err)
        return SendResult(False, None, error=str(err)[:300], error_code=str(r.status_code))

    def send_text(self, from_number, to, text):
        return self._post({"to": to_e164(to), "text": text})

    def send_media(self, from_number, to, media_url, caption=None):
        path = media_url.split("?")[0].lower()
        if path.endswith((".mp4", ".mov", ".3gp", ".webm")):
            key = "videoUrl"
        elif path.endswith((".mp3", ".ogg", ".opus", ".m4a", ".wav", ".aac")):
            key = "audioUrl"
        elif path.endswith((".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".zip", ".csv", ".txt")):
            key = "documentUrl"
        else:
            key = "imageUrl"
        payload = {"to": to_e164(to), key: media_url}
        if caption and key != "audioUrl":
            payload["text"] = caption
        return self._post(payload)

    def send_template(self, from_number, to, template_id, variables=None):
        return SendResult(
            False, None, error="Wasender has no approved templates — send normal text instead",
            error_code="template_unsupported",
        )


class MetaCloudWhatsAppService(WhatsAppServiceInterface):
    """Meta Cloud API — not implemented (Twilio is the chosen provider)."""

    name = "meta"

    def _not_implemented(self, *a, **k):
        raise NotImplementedError("Meta Cloud API is not implemented. Use WHATSAPP_PROVIDER=twilio.")

    send_text = send_media = send_template = _not_implemented


_twilio_singleton: TwilioWhatsAppService | None = None
_wasender_singleton: WasenderWhatsAppService | None = None


def get_whatsapp_service() -> WhatsAppServiceInterface:
    global _twilio_singleton, _wasender_singleton
    if settings.WHATSAPP_MOCK:
        return MockWhatsAppService()

    provider = settings.WHATSAPP_PROVIDER.lower()
    if provider == "twilio":
        if _twilio_singleton is None:
            _twilio_singleton = TwilioWhatsAppService()
        return _twilio_singleton
    if provider == "wasender":
        if _wasender_singleton is None:
            _wasender_singleton = WasenderWhatsAppService()
        return _wasender_singleton
    if provider == "meta":
        return MetaCloudWhatsAppService()
    raise ValueError(f"Unsupported WhatsApp provider: {settings.WHATSAPP_PROVIDER}")
