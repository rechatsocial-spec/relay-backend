"""
"Check connection": asks the WhatsApp service about everything between a customer's text and this app, with the
server's own API key, and says what is wrong in plain words.

  1. the API key works, and which accounts (numbers) it can see and whether they are connected
  2. this server's public address and webhook secret are set, and the address answers a signed test from outside
  3. the webhook endpoint registered at the service: exists, enabled, listens for incoming messages, covers every
     number, and uses the SAME secret as this server (the secret itself is never shown)
  4. what the service reports about its recent deliveries to the endpoint (HTTP answers, timeouts)
  5. what this server has actually received
"""
import hashlib
import hmac
import json
import time

import httpx

from app.core.config import settings
from app.services import sync as _sync
from app.services import webhook_stats

NEEDED_EVENTS = ("message.new",)
NICE_EVENTS = ("message.receipt.delivery", "message.receipt.read", "account.status.disconnected")


def _list(path: str, params: dict | None = None, key: str | None = None) -> list:
    out, offset = [], 0
    while True:
        body = _sync._get(path, {**(params or {}), "limit": 100, "offset": offset}, key=key)
        data = body.get("data") or body.get("items") or []
        out += data
        if not body.get("has_more") or not data:
            return out
        offset += len(data)


def webhook_url() -> str:
    base = settings.PUBLIC_BASE_URL.rstrip("/")
    return f"{base}{settings.API_V1_PREFIX}/whatsapp/webhook" if base else ""


def _signed_post(url: str, event: dict) -> httpx.Response:
    raw = json.dumps(event).encode()
    ts = str(int(time.time()))
    sig = hmac.new(settings.UNIPILE_WEBHOOK_SECRET.encode(), f"{ts}.".encode() + raw, hashlib.sha256).hexdigest()
    return httpx.post(url, content=raw, headers={"content-type": "application/json", "unipile-signature": f"t={ts},v0={sig}"}, timeout=10)


def run_connection_check() -> dict:
    checks: list[dict] = []

    def add(key, label, status, detail, fix=None):
        checks.append({"key": key, "label": label, "status": status, "detail": detail, "fix": fix})

    url = webhook_url()
    if not _sync._is_live():
        import os

        near = sorted(k for k in os.environ if k.upper().startswith("UNIPILE") and "SECRET" not in k.upper())
        hint = f" Settings found with a similar name: {', '.join(near)}." if near else ""
        add("live", "WhatsApp is connected on the server", "fail", "No API key was found on the server, or demo mode is on." + hint,
            "Save the key in the server settings under the name UNIPILE_API_KEY and redeploy.")
        return {"ok": False, "webhook_url": url, "checks": checks}
    add("live", "WhatsApp is connected on the server", "ok", "An API key is set and demo mode is off.")

    # 1. key + accounts
    accounts = []
    try:
        accounts = _sync.list_accounts()
        if accounts:
            add("key", "API key and accounts", "ok", f"The key works and sees {len(accounts)} WhatsApp account{'s' if len(accounts) != 1 else ''}.")
        else:
            add("key", "API key and accounts", "fail", "The key works but sees no WhatsApp accounts.",
                "Use a key from the application that holds your numbers, and make it a Service Key.")
    except Exception as e:
        add("key", "API key and accounts", "fail", _sync.explain(e), "Fix the key in the server settings, then run the check again.")
    # a Scoped key only sees the accounts of one scope: compare with what the Service Key (all accounts) sees
    if accounts and settings.UNIPILE_SERVICE_KEY and settings.UNIPILE_SERVICE_KEY != settings.UNIPILE_API_KEY:
        try:
            everything = _sync.list_accounts(key=settings.UNIPILE_SERVICE_KEY)
            have = {a["id"] for a in accounts}
            hidden = [a for a in everything if a["id"] not in have]
            if hidden:
                add("scope", "The API key sees every number", "fail",
                    f"The API key sees {len(accounts)} of {len(everything)} accounts and cannot see: " + ", ".join((a.get("name") or a["id"]) for a in hidden)
                    + ". A Scoped key only sees the accounts of one scope.",
                    "Replace the API key with a Global Account API key (all accounts), or with the Service Key.")
            else:
                add("scope", "The API key sees every number", "ok", f"The API key sees all {len(everything)} accounts.")
        except Exception as e:
            add("scope", "The API key sees every number", "warn", f"Could not compare with the Service Key ({_sync.explain(e)}).")
    for a in accounts:
        phone = _sync._account_phone(a) or ""
        label = f"{a.get('name') or phone or a['id']}{' (' + phone + ')' if phone and phone != a.get('name') else ''}"
        state = a.get("status") or "unknown"
        if state == "running" and not a.get("is_locked"):
            add(f"acc:{a['id']}", f"Number {label}", "ok", "Connected.")
        else:
            add(f"acc:{a['id']}", f"Number {label}", "fail", f"Not connected right now (status: {state}{', locked' if a.get('is_locked') else ''}). Messages to it cannot reach this app.",
                "Reconnect the number in the WhatsApp connection service (scan its QR code again).")

    # 2. address + secret + signed self-test
    if not settings.PUBLIC_BASE_URL:
        add("address", "This server's public address", "fail", "The public address is not set, so the service has nowhere to be told about new messages.",
            "Set the public address of this server in the server settings.")
    elif not settings.UNIPILE_WEBHOOK_SECRET:
        add("secret", "Webhook secret", "fail", "No webhook secret is set on the server, so every notification would be refused.", "Copy the endpoint's secret into the server settings.")
    else:
        try:
            r = _signed_post(url, {"type": "connection.test", "account_id": None, "payload": {}})
            if r.status_code == 200:
                add("address", "Notification address answers", "ok", f"A signed test message to {url} was accepted.")
            else:
                add("address", "Notification address answers", "fail", f"A signed test message to {url} was answered with HTTP {r.status_code}.",
                    "Check that the public address is exactly this server's address (https, no extra path).")
        except Exception as e:
            add("address", "Notification address answers", "fail", f"Could not reach {url} from the server itself ({e.__class__.__name__}).",
                "Check the public address setting; the service must be able to open it from the internet.")

    # 3. the endpoint registered at the service
    endpoint = None
    try:
        endpoints = _list("/webhooks/endpoints/", key=settings.management_key)
        ours = {url.rstrip("/"), url.replace("/whatsapp/", "/unipile/").rstrip("/")} if url else set()
        matching = [e for e in endpoints if (e.get("url") or "").rstrip("/") in ours]
        if not matching:
            others = ", ".join(sorted({e.get("url", "?") for e in endpoints})[:4]) or "none"
            add("endpoint", "Webhook endpoint at the service", "fail", f"No webhook endpoint points to {url or 'this server'}. Endpoints that exist: {others}.",
                f"In the WhatsApp connection service, add a webhook endpoint with exactly this address: {url}")
        else:
            endpoint = next((e for e in matching if e.get("enabled")), matching[0])
            add("endpoint", "Webhook endpoint at the service", "ok" if endpoint.get("enabled") else "fail",
                "Found and enabled." if endpoint.get("enabled") else "Found, but it is switched off.", None if endpoint.get("enabled") else "Enable the endpoint.")
            events = set(endpoint.get("trigger_events") or [])
            missing = [e for e in NEEDED_EVENTS if e not in events]
            add("events", "It listens for incoming messages", "ok" if not missing else "fail",
                "Subscribed to new messages." if not missing else f"Not subscribed to: {', '.join(missing)}. Texts you receive are never announced to this app.",
                None if not missing else "Edit the endpoint and tick the new-message event.")
            nice = [e for e in NICE_EVENTS if e not in events]
            if nice:
                add("events2", "Delivery ticks and connection changes", "warn", f"Not subscribed to: {', '.join(nice)}.", "Tick them too so ticks and disconnections show up instantly.")
            ids = endpoint.get("account_ids") or []
            if ids:
                ours_ids = {a["id"] for a in accounts}
                lacking = [a for a in accounts if a["id"] not in ids]
                add("coverage", "It covers every number", "ok" if not lacking else "fail",
                    "All numbers are covered." if not lacking else "Not covered: " + ", ".join((a.get("name") or a["id"]) for a in lacking) + ". Texts to those numbers are never announced.",
                    None if not lacking else "Edit the endpoint and add those accounts (or leave the account list empty to cover all).")
            else:
                add("coverage", "It covers every number", "ok", "The endpoint is not limited to certain accounts, so it covers all of them.")
            secret = endpoint.get("secret")
            if secret is None:
                add("secretmatch", "Secret matches", "warn", "The service did not show the endpoint's secret, so it cannot be compared.")
            else:
                same = hmac.compare_digest(str(secret), settings.UNIPILE_WEBHOOK_SECRET or "")
                add("secretmatch", "Secret matches", "ok" if same else "fail",
                    "The secret on the server is the same as the endpoint's." if same else "The secret on the server is DIFFERENT from the endpoint's, so every notification is refused as forged.",
                    None if same else "Copy the endpoint's secret into the server settings and redeploy.")
            # 4. what the service says happened to recent notifications
            try:
                deliveries = _sync._get("/webhooks/conversations/", {"endpoint_id": endpoint["id"], "limit": 25}, key=settings.management_key).get("data") or []
                if not deliveries:
                    add("deliveries", "Recent notifications from the service", "warn", "The service has not tried to deliver anything to this endpoint in the last days.",
                        "Send a text to one of your numbers and run the check again.")
                else:
                    bad = [d for d in deliveries if not (d.get("http_status") and 200 <= d["http_status"] < 300)]
                    if not bad:
                        add("deliveries", "Recent notifications from the service", "ok", f"The last {len(deliveries)} notifications were all accepted by this server.")
                    else:
                        codes = {}
                        for d in bad:
                            codes[d.get("http_status")] = codes.get(d.get("http_status"), 0) + 1
                        desc = ", ".join(f"{'no answer (timeout)' if c is None else 'HTTP ' + str(c)} x{n}" for c, n in codes.items())
                        hint = "403 means the secret differs. A timeout means this server took longer than 5 seconds to answer (is it asleep?)." if (403 in codes or None in codes) else "Look at the endpoint's delivery log at the service."
                        add("deliveries", "Recent notifications from the service", "fail", f"{len(bad)} of the last {len(deliveries)} notifications failed: {desc}.", hint)
            except Exception as e:
                add("deliveries", "Recent notifications from the service", "warn", f"Could not read the delivery log ({_sync.explain(e)}).")
    except Exception as e:
        has_service = bool(settings.UNIPILE_SERVICE_KEY)
        add("endpoint", "Webhook endpoint at the service", "warn", f"The webhook settings could not be read ({_sync.explain(e)}).",
            ("Check the Service Key saved on the server is valid." if has_service else
             "Save a Service Key on the server (UNIPILE_SERVICE_KEY): only a Service Key may read webhook settings.")
            + " Meanwhile, check in the service's dashboard that an endpoint with the address above exists, is enabled, listens for new messages and covers all numbers.")

    # 5. what this server has seen
    st = webhook_stats.STATS
    if st["received"]:
        add("seen", "Notifications this server has received", "ok", f"{st['received']} since it last started; the last one ({st['last_type']}) at {st['last_at']}.")
    else:
        add("seen", "Notifications this server has received", "warn", "None yet since this server last started (it restarts when you deploy).",
            "Text one of your numbers from another phone and run the check again.")
    if st["rejected_signature"] or st["rejected_stale"]:
        add("rejected", "Notifications this server refused", "fail", f"{st['rejected_signature']} had a wrong signature and {st['rejected_stale']} a stale timestamp (last: {st['last_rejected_at']}).",
            "A wrong signature almost always means the secret differs between the server and the endpoint.")
    if st["unknown_account"]:
        add("unknown", "Notifications for numbers not imported", "warn", f"{st['unknown_account']} were for an account that was not a number here yet ({st['last_unknown_account']}); accounts were imported again.",
            "Press Refresh on the WhatsApp Numbers page.")
    return {"ok": all(c["status"] in ("ok", "warn") for c in checks), "webhook_url": url, "checks": checks}
