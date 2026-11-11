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
             otherwise sends one of node.messageVariants (or the built-in text for the label) (rotating, or random if the step says so; {{name}} works).
  delay      pauses the run: node.delay_minutes, else parsed from the label
             ("Wait 2 hours", "30 min", "1 day"), else 60 minutes.
  condition  "Customer replied?": true if the customer wrote after the trigger.
             Then only the following nodes whose `sub` is "YES" (or "NO") run,
             and the run ends.
Every message step first waits a random 15-30 s (AUTOMATION_REPLY_DELAY_*, or the step's `replyDelay`)
so automated replies don't arrive instantly. Opted-out contacts are never messaged.
"""
import logging
import random
import re
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.core.automation_templates import DEFAULT_FLOW, default_message_for
from app.core.config import settings
from app.db.session import SessionLocal
from app.models.automation import Automation, AutomationRun, AutomationStatus
from app.models.contact import Contact
from app.models.user import Role, User
from app.models.conversation import Conversation, ConversationStatus, Message, MessageSender, MessageStatus, MessageType
from app.models.whatsapp_number import WhatsAppNumber
from app.services.messaging import send_message, utcnow
from app.services.whatsapp_service import get_whatsapp_service

log = logging.getLogger("relay.automations")

POLL_SECONDS = 1  # fine-grained: the random reply delays are only seconds long
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


def _owner_ids(db: Session, number: WhatsAppNumber) -> list:
    """Whose automations apply to this number: its admin's, and any Super Admin's (a number is imported under a Super
    Admin and may later be assigned to an Admin; an automation made by a Super Admin must still answer its customers)."""
    supers = [i for (i,) in db.query(User.id).filter(User.role == Role.SUPER_ADMIN)]
    return list({number.admin_id, *supers})


def start_new_customer_automations(db: Session, number: WhatsAppNumber, contact: Contact, conv: Conversation) -> int:
    """Queues a run per Running `new_customer` automation. Does not commit. Returns how many."""
    autos = (
        db.query(Automation)
        .filter(Automation.admin_id.in_(_owner_ids(db, number)), Automation.trigger_type == "new_customer",
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


def _has_keyword(text: str, keywords: list[str]) -> bool:
    low = (text or "").lower()
    return any(re.search(rf"(?<!\w){re.escape(k.strip().lower())}(?!\w)", low) for k in keywords if k and k.strip())


def start_keyword_automations(db: Session, number: WhatsAppNumber, contact: Contact, conv: Conversation, text: str) -> int:
    """Queues a run per Running `keyword` automation whose keywords appear in this message (whole words,
    any case). At most once per chat per automation per 24 hours, so a chatty customer is not answered twice.
    Does not commit. Returns how many were queued."""
    if not (text or "").strip():
        return 0
    autos = (
        db.query(Automation)
        .filter(Automation.admin_id.in_(_owner_ids(db, number)), Automation.trigger_type == "keyword", Automation.status == AutomationStatus.RUNNING)
        .all()
    )
    now = utcnow()
    queued = 0
    for a in autos:
        if not _has_keyword(text, (a.trigger_config or {}).get("keywords") or []):
            continue
        since = now - timedelta(hours=24)
        recent = db.query(AutomationRun).filter(AutomationRun.automation_id == a.id, AutomationRun.conversation_id == conv.id,
                                                AutomationRun.started_at >= since).first()
        if recent:
            continue
        db.add(AutomationRun(automation_id=a.id, contact_id=contact.id, conversation_id=conv.id, whatsapp_number_id=number.id,
                             next_index=1, run_at=now, started_at=now, status="pending"))
        a.recipients = (a.recipients or 0) + 1
        queued += 1
    return queued


def _handoff(db: Session, automation: Automation, conv: Conversation) -> None:
    conv.status = ConversationStatus.NEEDS_HUMAN
    db.add(Message(conversation_id=conv.id, sender=MessageSender.SYSTEM, type=MessageType.NOTE,
                   text=f"Automation \"{automation.name}\" handed this chat to a human", status=MessageStatus.SENT))


_HANDOFF_WORDS = ("human", "transfer", "assign")


def is_message_step(node: dict) -> bool:
    return node.get("type") == "action" and not any(w in (node.get("label") or "").lower() for w in _HANDOFF_WORDS)


def reply_delay(node: dict) -> tuple[float, float]:
    """(min, max) seconds to wait before a message step is sent: the step's `replyDelay`, else the global default."""
    lo, hi = float(settings.AUTOMATION_REPLY_DELAY_MIN), float(settings.AUTOMATION_REPLY_DELAY_MAX)
    custom = node.get("replyDelay")
    if isinstance(custom, dict):
        try:
            lo, hi = float(custom.get("min", lo)), float(custom.get("max", hi))
        except (TypeError, ValueError):
            pass
    lo = max(0.0, min(lo, 3600.0))
    return lo, max(lo, min(hi, 3600.0))


def _wait_first(run: AutomationRun, idx: int, node: dict) -> bool:
    """Before a message step: schedule a random 'typing' wait. True = wait scheduled, the run comes back later to send."""
    if not is_message_step(node) or run.jitter_done == idx:
        return False
    lo, hi = reply_delay(node)
    if hi <= 0:
        return False
    run.jitter_done = idx
    # counted from the moment this step became due (the customer's message / the end of the previous wait),
    # not from when the poller got to it, so the reply really lands 15-30 s after
    due = _aware(run.run_at) or utcnow()
    run.run_at = due + timedelta(seconds=random.uniform(lo, hi))
    return True


def _run_branch(db: Session, automation: Automation, run: AutomationRun, nodes: list, contact: Contact,
                conv: Conversation, number: WhatsAppNumber) -> None:
    """The steps of the chosen YES/NO branch, one at a time (each message waits its own random time)."""
    queue = list(run.branch_queue or [])
    while queue:
        j = queue[0]
        node = nodes[j] if j < len(nodes) and isinstance(nodes[j], dict) else {}
        if _wait_first(run, j, node):
            run.branch_queue = queue
            return
        _do_action(db, automation, run, node, contact, conv, number)
        queue.pop(0)
    run.branch_queue = None
    run.status = "done"


def _do_action(db: Session, automation: Automation, run: AutomationRun, node: dict,
               contact: Contact, conv: Conversation, number: WhatsAppNumber) -> None:
    label = (node.get("label") or "").lower()
    if any(w in label for w in ("human", "transfer", "assign")):
        _handoff(db, automation, conv)
        return
    variants = [v for v in (node.get("messageVariants") or []) if isinstance(v, str) and v.strip()]
    if not variants:  # no text written for this step: use the built-in wording for its label
        variants = [default_message_for(node.get("label"))]
    if node.get("assignmentMode") == "random":
        text = random.choice(variants)
    else:  # round-robin (the builder's default): rotate through the variants
        text = variants[(automation.sent or 0) % len(variants)]
    msg = send_message(db, get_whatsapp_service(), number=number, contact=contact, conversation=conv,
                       text=_personalize(text, contact))
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

    nodes = (automation.flow or {}).get("nodes") or DEFAULT_FLOW["nodes"]  # nothing saved: the built-in flow
    if run.branch_queue:  # in the middle of a YES/NO branch
        _run_branch(db, automation, run, nodes, contact, conv, number)
        return
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
            run.branch_queue = [j for j in range(i + 1, len(nodes)) if isinstance(nodes[j], dict) and nodes[j].get("type") == "action"
                                and (nodes[j].get("sub") or "").upper() == branch]
            _run_branch(db, automation, run, nodes, contact, conv, number)
            return
        if kind == "action":
            if _wait_first(run, i, node):
                run.next_index = i  # come back to this step once the wait is over
                return
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
