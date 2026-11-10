"""
Runs Automations (the flow builder's JSON) when a new customer writes in.

Trigger  `new_customer`: an inbound WhatsApp message from someone who isn't a
contact yet (see endpoints/unipile.py). Every *Running* automation of the
number's admin with that trigger gets a persisted AutomationRun; a small
poller thread (start_poller, called on startup) executes due runs, so waits
("Wait 2 hours") survive restarts. Single API instance only, like campaigns.

How the flow's nodes (a linear list, as saved by the builder) are executed:
  trigger    skipped
  action     "…human/transfer/assign…" in the label -> hand the chat to a human;
             otherwise sends one of node.messageVariants (random, {{name}} works).
             An action with no message text is skipped and noted in the thread.
  delay      pauses the run: node.delay_minutes, else parsed from the label
             ("Wait 2 hours", "30 min", "1 day"), else 60 minutes.
  condition  "Customer replied?": true if the customer wrote after the trigger.
             Then only the following nodes whose `sub` is "YES" (or "NO") run,
             and the run ends.
Opted-out contacts are never messaged.
"""
import logging
import random
import re
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models.automation import Automation, AutomationRun, AutomationStatus
from app.models.contact import Contact
from app.models.conversation import Conversation, ConversationStatus, Message, MessageSender, MessageStatus, MessageType
from app.models.whatsapp_number import WhatsAppNumber
from app.services.messaging import send_message, utcnow
from app.services.whatsapp_service import get_whatsapp_service

log = logging.getLogger("relay.automations")

POLL_SECONDS = 5
DEFAULT_DELAY_MINUTES = 60
_UNITS = {"m": 1, "h": 60, "d": 1440}


def _aware(dt: datetime | None) -> datetime | None:
    return dt.replace(tzinfo=timezone.utc) if dt is not None and dt.tzinfo is None else dt  # SQLite is naive


def parse_delay_minutes(node: dict) -> int:
    if isinstance(node.get("delay_minutes"), (int, float)):
        return max(int(node["delay_minutes"]), 0)
    m = re.search(r"(\d+)\s*(min|minute|m|hour|hr|h|day|d)", (node.get("label") or "").lower())
    if not m:
        return DEFAULT_DELAY_MINUTES
    return int(m.group(1)) * _UNITS[m.group(2)[0]]


def _personalize(text: str, contact: Contact) -> str:
    first = (contact.name or "").split(" ")[0] or "there"
    return text.replace("{{name}}", first).replace("{{full_name}}", contact.name or "")


def start_new_customer_automations(db: Session, number: WhatsAppNumber, contact: Contact, conv: Conversation) -> int:
    """Queues a run per Running `new_customer` automation. Does not commit. Returns how many."""
    autos = (
        db.query(Automation)
        .filter(Automation.admin_id == number.admin_id, Automation.trigger_type == "new_customer",
                Automation.status == AutomationStatus.RUNNING)
        .all()
    )
    now = utcnow()
    for a in autos:
        db.add(AutomationRun(
            automation_id=a.id, contact_id=contact.id, conversation_id=conv.id,
            whatsapp_number_id=number.id, next_index=1, run_at=now, started_at=now, status="pending",
        ))
        a.recipients = (a.recipients or 0) + 1
    return len(autos)


def _handoff(db: Session, automation: Automation, conv: Conversation) -> None:
    conv.status = ConversationStatus.NEEDS_HUMAN
    db.add(Message(conversation_id=conv.id, sender=MessageSender.SYSTEM, type=MessageType.NOTE,
                   text=f"Automation \"{automation.name}\" handed this chat to a human", status=MessageStatus.SENT))


def _do_action(db: Session, automation: Automation, run: AutomationRun, node: dict,
               contact: Contact, conv: Conversation, number: WhatsAppNumber) -> None:
    label = (node.get("label") or "").lower()
    if any(w in label for w in ("human", "transfer", "assign")):
        _handoff(db, automation, conv)
        return
    variants = [v for v in (node.get("messageVariants") or []) if isinstance(v, str) and v.strip()]
    if not variants:
        db.add(Message(conversation_id=conv.id, sender=MessageSender.SYSTEM, type=MessageType.NOTE,
                       text=f"Automation \"{automation.name}\": step \"{node.get('label')}\" has no message text — skipped",
                       status=MessageStatus.SENT))
        return
    msg = send_message(db, get_whatsapp_service(), number=number, contact=contact, conversation=conv,
                       text=_personalize(random.choice(variants), contact))
    if msg.provider_message_id:
        automation.sent = (automation.sent or 0) + 1


def _advance(db: Session, run: AutomationRun) -> None:
    automation = db.get(Automation, run.automation_id)
    contact = db.get(Contact, run.contact_id)
    conv = db.get(Conversation, run.conversation_id)
    number = db.get(WhatsAppNumber, run.whatsapp_number_id)
    if not (automation and contact and conv and number) or automation.status != AutomationStatus.RUNNING or contact.opted_out:
        run.status = "done"  # paused/deleted automation or opted-out contact: stop quietly
        return

    nodes = (automation.flow or {}).get("nodes") or []
    i = run.next_index
    while i < len(nodes):
        node = nodes[i] if isinstance(nodes[i], dict) else {}
        kind = node.get("type")
        if kind == "delay":
            run.next_index = i + 1
            run.run_at = utcnow() + timedelta(minutes=parse_delay_minutes(node))
            return
        if kind == "condition":
            last_in = _aware(conv.last_inbound_at)
            replied = bool(last_in and last_in > _aware(run.started_at))
            if replied:
                automation.replies = (automation.replies or 0) + 1
            branch = "YES" if replied else "NO"
            for n in nodes[i + 1:]:
                if isinstance(n, dict) and n.get("type") == "action" and (n.get("sub") or "").upper() == branch:
                    _do_action(db, automation, run, n, contact, conv, number)
            run.status = "done"
            return
        if kind == "action":
            _do_action(db, automation, run, node, contact, conv, number)
        i += 1
    run.status = "done"


def process_due_runs(limit: int = 50) -> int:
    """Executes runs whose time has come. Returns how many were processed."""
    db = SessionLocal()
    processed = 0
    try:
        runs = (
            db.query(AutomationRun)
            .filter(AutomationRun.status == "pending", AutomationRun.run_at <= utcnow())
            .order_by(AutomationRun.run_at).limit(limit).all()
        )
        for run in runs:
            try:
                _advance(db, run)
            except Exception:
                log.exception("Automation run %s failed", run.id)
                db.rollback()
                run = db.get(AutomationRun, run.id)
                run.status = "failed"
            db.commit()
            processed += 1
    finally:
        db.close()
    return processed


_poller_started = False


def start_poller() -> None:
    global _poller_started
    if _poller_started:
        return
    _poller_started = True

    def loop():
        while True:
            try:
                process_due_runs()
            except Exception:
                log.exception("Automation poller error")
            time.sleep(POLL_SECONDS)

    threading.Thread(target=loop, daemon=True, name="automation-poller").start()
