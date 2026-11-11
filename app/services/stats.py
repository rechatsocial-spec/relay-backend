"""
Real figures, counted from the messages table — nothing is estimated or stored
as a running counter.

  sent       outbound (business / bot) messages that went out (sent, delivered or read)
  delivered  outbound messages delivered or read
  read       outbound messages read
  failed     outbound messages that failed
  received   messages the customers wrote (shown as "replies")
"""
from datetime import datetime, timedelta, timezone

from sqlalchemy import case, func
from sqlalchemy.orm import Session

from app.models.conversation import Conversation, Message, MessageSender, MessageStatus, MessageType

_OUT = (MessageSender.BUSINESS, MessageSender.BOT)
_SENT = (MessageStatus.SENT, MessageStatus.DELIVERED, MessageStatus.READ)
_DELIVERED = (MessageStatus.DELIVERED, MessageStatus.READ)
_FAILED = (MessageStatus.FAILED, MessageStatus.UNDELIVERED)


def _sum(cond):
    return func.coalesce(func.sum(case((cond, 1), else_=0)), 0)


def _columns(since=None):
    out = Message.sender.in_(_OUT)
    in_range = (Message.created_at >= since) if since is not None else (Message.id.isnot(None))
    return [
        _sum(out & Message.status.in_(_SENT) & in_range),
        _sum(out & Message.status.in_(_DELIVERED) & in_range),
        _sum(out & (Message.status == MessageStatus.READ) & in_range),
        _sum(out & Message.status.in_(_FAILED) & in_range),
        _sum((Message.sender == MessageSender.CUSTOMER) & in_range),
    ]


def _scoped(q, admin_id, number_id):
    q = q.join(Conversation, Conversation.id == Message.conversation_id).filter(Message.type != MessageType.NOTE)
    if admin_id:
        q = q.filter(Conversation.admin_id == admin_id)
    if number_id:
        q = q.filter(Conversation.whatsapp_number_id == number_id)
    return q


def message_counts(db: Session, admin_id=None, number_id=None) -> dict:
    row = _scoped(db.query(*_columns()), admin_id, number_id).one()
    return dict(zip(("sent", "delivered", "read", "failed", "received"), (int(x or 0) for x in row)))


def daily_series(db: Session, days: int = 14, admin_id=None, number_id=None) -> list[dict]:
    today = datetime.now(timezone.utc).date()
    first = today - timedelta(days=days - 1)
    start = datetime(first.year, first.month, first.day, tzinfo=timezone.utc)
    q = _scoped(db.query(func.date(Message.created_at), *_columns()), admin_id, number_id)
    rows = q.filter(Message.created_at >= start).group_by(func.date(Message.created_at)).all()
    by_day = {str(r[0])[:10]: r[1:] for r in rows}
    series = []
    for i in range(days):
        d = (first + timedelta(days=i)).isoformat()
        sent, delivered, read, failed, received = (int(x or 0) for x in by_day.get(d, (0, 0, 0, 0, 0)))
        series.append({"date": d, "sent": sent, "delivered": delivered, "read": read, "received": received})
    return series


def number_stats(db: Session, number_ids: list) -> dict:
    """Per number: messages sent today (UTC), delivery rate %, reply rate % (chats we wrote to that answered)."""
    if not number_ids:
        return {}
    now = datetime.now(timezone.utc)
    midnight = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
    out = Message.sender.in_(_OUT)
    rows = (
        db.query(
            Conversation.whatsapp_number_id,
            _sum(out & Message.status.in_(_SENT)),
            _sum(out & Message.status.in_(_DELIVERED)),
            _sum(out & Message.status.in_(_SENT) & (Message.created_at >= midnight)),
        )
        .join(Message, Message.conversation_id == Conversation.id)
        .filter(Conversation.whatsapp_number_id.in_(number_ids), Message.type != MessageType.NOTE)
        .group_by(Conversation.whatsapp_number_id).all()
    )
    pairs = (
        db.query(Conversation.whatsapp_number_id, Message.conversation_id, Message.sender)
        .join(Message, Message.conversation_id == Conversation.id)
        .filter(Conversation.whatsapp_number_id.in_(number_ids), Message.type != MessageType.NOTE,
                Message.sender.in_(_OUT + (MessageSender.CUSTOMER,)))
        .distinct().all()
    )
    wrote, replied = {}, {}
    for nid, cid, sender in pairs:
        (replied if sender == MessageSender.CUSTOMER else wrote).setdefault(nid, set()).add(cid)
    stats = {}
    for nid, sent, delivered, today in rows:
        sent, delivered = int(sent or 0), int(delivered or 0)
        w = wrote.get(nid, set())
        stats[nid] = {
            "messages_today": int(today or 0),
            "delivery_rate": round(delivered / sent * 100, 1) if sent else 0.0,
            "reply_rate": round(len(w & replied.get(nid, set())) / len(w) * 100, 1) if w else 0.0,
        }
    return stats
