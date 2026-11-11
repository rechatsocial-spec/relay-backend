"""
Every route that sends a WhatsApp message (inbox replies, campaign dispatch)
calls get_whatsapp_service(), never the provider directly.

The only real provider is Unipile v2 (https://developer.unipile.com/v2.0/docs/).
WHATSAPP_MOCK=true swaps in a fake that "succeeds" without any network call —
for local development only; set WHATSAPP_MOCK=false in production.

Every send takes `from_number` = the CRM WhatsAppNumber.phone the message goes
out from; that number's `unipile_account_id` picks the linked WhatsApp account.
"""
from __future__ import annotations

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
    def send_text(self, from_number: str, to: str, text: str, chat_id: str | None = None) -> SendResult: ...

    @abstractmethod
    def send_media(self, from_number: str, to: str, media_url: str, caption: str | None = None, chat_id: str | None = None) -> SendResult:
        """Image / video / voice note / sticker / document — WhatsApp decides by the file's type."""

    @abstractmethod
    def send_attachment(self, from_number: str, to: str, data: bytes, content_type: str, filename: str,
                        caption: str | None = None, voice_note: bool = False, duration: int | None = None,
                        chat_id: str | None = None) -> SendResult:
        """A file picked or recorded in the inbox: photo, video, document or voice note."""

    @abstractmethod
    def send_template(self, from_number: str, to: str, template_id: str, variables: dict | None = None) -> SendResult:
        """Pre-approved template — the only way to message someone outside the 24h window."""


class MockWhatsAppService(WhatsAppServiceInterface):
    """Simulates the provider: always 'succeeds', never makes a network call."""

    name = "mock"

    def _mock(self) -> SendResult:
        return SendResult(success=True, provider_message_id=f"mock_{uuid.uuid4().hex[:12]}")

    def send_text(self, from_number, to, text, chat_id=None):
        return self._mock()

    def send_media(self, from_number, to, media_url, caption=None, chat_id=None):
        return self._mock()

    def send_attachment(self, from_number, to, data, content_type, filename, caption=None, voice_note=False, duration=None, chat_id=None):
        return self._mock()

    def send_template(self, from_number, to, template_id, variables=None):
        return self._mock()


class UnipileWhatsAppService(WhatsAppServiceInterface):
    """
    Sends through Unipile v2 (https://developer.unipile.com/v2.0/docs/) — a
    normal WhatsApp account linked by QR / pairing code through Hosted Auth.
    Docs: https://developer.unipile.com/v2.0/docs/whatsapp-send-messages

    - Auth: X-API-KEY header (one key for the whole app).
    - `from_number` picks the linked account: the CRM WhatsAppNumber with that
      phone must have a `unipile_account_id` (set by the link flow).
    - No templates, no 24h window; pace campaigns to avoid bans.
    - Recipient id = digits + "@s.whatsapp.net". Sent through POST
      /{account_id}/chats/{chat_id}/messages/send (chat_id = the id Unipile returned for the
      chat, stored on the conversation; for a new chat the number@s.whatsapp.net form); a 404 means no chat yet, so POST /{account_id}/chats/send starts one.
    - Attachments must be base64, so media_url is downloaded first.
    """

    name = "unipile"

    def __init__(self, http=None):
        if http is None:
            import httpx

            if not settings.UNIPILE_API_KEY:
                raise RuntimeError("WhatsApp is not configured on the server (missing API key)")
            http = httpx.Client(timeout=60)
        self.http = http

    @staticmethod
    def _account_id_for(from_number: str) -> str | None:
        from app.db.session import SessionLocal
        from app.models.whatsapp_number import WhatsAppNumber

        target = to_e164(from_number)
        db = SessionLocal()
        try:
            for n in db.query(WhatsAppNumber).filter(WhatsAppNumber.unipile_account_id.isnot(None)).all():
                if to_e164(n.phone) == target:
                    return n.unipile_account_id
        finally:
            db.close()
        return None

    @staticmethod
    def _chat_id_for(account_id: str, to: str) -> str | None:
        """Chat id Unipile gave us for this contact (e.g. an @lid id) — used as-is."""
        from app.db.session import SessionLocal
        from app.models.contact import Contact
        from app.models.conversation import Conversation
        from app.models.whatsapp_number import WhatsAppNumber

        target = to_e164(to)
        db = SessionLocal()
        try:
            rows = (
                db.query(Conversation.provider_chat_id, Contact.phone)
                .join(Contact, Contact.id == Conversation.contact_id)
                .join(WhatsAppNumber, WhatsAppNumber.id == Conversation.whatsapp_number_id)
                .filter(WhatsAppNumber.unipile_account_id == account_id, Conversation.provider_chat_id.isnot(None))
                .order_by(Conversation.last_message_at.desc())
                .all()
            )
            for chat_id, phone in rows:
                if to_e164(phone) == target:
                    return chat_id
        finally:
            db.close()
        return None

    def _find_chat(self, base: str, headers: dict, jid: str) -> str | None:
        """GET /{account}/users/{user_id}/chat: the existing conversation with this person, if there is one."""
        try:
            r = self.http.get(f"{base}/users/{jid}/chat", headers=headers)
            if r.status_code >= 300:
                return None
            body = r.json()
            found = body.get("id") or (body.get("data") or {}).get("id") if isinstance(body, dict) else None
            return str(found) if found else None
        except Exception:
            return None

    def _send(self, from_number: str, to: str, text: str, attachments: list | None = None, chat_id: str | None = None) -> SendResult:
        account_id = self._account_id_for(from_number)
        if not account_id:
            return SendResult(False, None, error=f"{from_number} is not linked yet — link the number first",
                              error_code="unipile_not_linked")
        jid = f"{to_e164(to).lstrip('+')}@s.whatsapp.net"
        chat_id = chat_id or self._chat_id_for(account_id, to)  # the chat's own id when we know it (@lid people)
        base = f"{settings.UNIPILE_BASE_URL.rstrip('/')}/{account_id}"
        headers = {"X-API-KEY": settings.UNIPILE_API_KEY, "accept": "application/json"}
        try:
            body_out = {"text": text or ""}
            if attachments:
                body_out["attachments"] = attachments
            if not chat_id:
                chat_id = self._find_chat(base, headers, jid)  # "Find Chats by User": reuse the chat that already exists
            r = None
            if chat_id:
                r = self.http.post(f"{base}/chats/{chat_id}/messages/send", json=body_out, headers=headers)
            if r is None or r.status_code == 404:
                # No chat with this person yet -> start one with the first message (number@s.whatsapp.net for a new conversation).
                r = self.http.post(f"{base}/chats/send", json={"users_ids": jid, **body_out}, headers=headers)
            try:
                body = r.json()
            except Exception:
                body = {}
        except Exception as e:  # network errors, bad config
            log.exception("Unipile send crashed")
            return SendResult(False, None, error=str(e)[:300])

        if r.status_code < 300:
            mid = body.get("message_id") or body.get("id")
            if isinstance(mid, list):
                mid = mid[0] if mid else None
            return SendResult(True, str(mid) if mid else f"unipile_{uuid.uuid4().hex[:12]}")
        err = body.get("detail") or body.get("message") or body.get("title") or r.text or f"HTTP {r.status_code}"
        log.warning("Unipile send failed (%s): %s", r.status_code, err)
        return SendResult(False, None, error=str(err)[:300], error_code=str(r.status_code))

    def send_text(self, from_number, to, text, chat_id=None):
        return self._send(from_number, to, text, chat_id=chat_id)

    def send_media(self, from_number, to, media_url, caption=None, chat_id=None):
        import base64
        import mimetypes
        from urllib.parse import urlparse

        try:
            dl = self.http.get(media_url, follow_redirects=True)
            dl.raise_for_status()
        except Exception as e:
            return SendResult(False, None, error=f"Could not download media: {e}"[:300])
        filename = urlparse(media_url).path.rsplit("/", 1)[-1] or "file"
        content_type = (dl.headers.get("content-type") or "").split(";")[0] or mimetypes.guess_type(filename)[0] or "application/octet-stream"
        attachment = {"content": base64.b64encode(dl.content).decode(), "content_type": content_type, "filename": filename}
        return self._send(from_number, to, caption or "", [attachment], chat_id=chat_id)

    def send_attachment(self, from_number, to, data, content_type, filename, caption=None, voice_note=False, duration=None, chat_id=None):
        import base64

        attachment = {"content": base64.b64encode(data).decode(), "content_type": content_type, "filename": filename}
        if voice_note:
            attachment["voice_note"] = True  # plays as a voice message
        if duration:
            attachment["metadata"] = {"duration": int(duration)}
        return self._send(from_number, to, caption or "", [attachment], chat_id=chat_id)

    def send_template(self, from_number, to, template_id, variables=None):
        return SendResult(
            False, None, error="Templates are not supported for this WhatsApp connection — send normal text instead",
            error_code="template_unsupported",
        )


_unipile_singleton: UnipileWhatsAppService | None = None


def get_whatsapp_service() -> WhatsAppServiceInterface:
    global _unipile_singleton
    if settings.WHATSAPP_MOCK:
        return MockWhatsAppService()
    if _unipile_singleton is None:
        _unipile_singleton = UnipileWhatsAppService()
    return _unipile_singleton
