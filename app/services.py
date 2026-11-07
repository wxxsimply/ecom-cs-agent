from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
from datetime import timedelta
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import dumps, utcnow
from app.models import (
    ChatInterrupt,
    ChatSession,
    ConfirmToken,
    Feedback,
    Message,
    Order,
    QuestionEvent,
    Refund,
    Ticket,
    User,
)
from app.refunds import RefundDecision

CONFIRM_SECRET = b"p0-lite-demo-confirm-secret-change-me"

PROGRESS_STEPS = [
    ("placed", "已下单"),
    ("paid", "已支付"),
    ("packed", "已出库"),
    ("picked_up", "已揽收"),
    ("in_transit", "运输中"),
    ("out_for_delivery", "派送中"),
    ("delivered", "已签收"),
]
PROGRESS_HINT = "以下为预计流程，实际以承运商轨迹为准"


def pii_scrub(text: str) -> str:
    text = re.sub(r"(?<!\d)1[3-9]\d{9}(?!\d)", "138****0000", text or "")
    text = re.sub(r"(?<!\d)\d{17}[\dXx](?!\d)", "******************", text)
    text = re.sub(r"(地址|收货地址)\s*[:：]?\s*[^\n，。]{4,}", r"\1：***", text)
    return text


def hash_user(user_id: str) -> str:
    return hmac.new(b"p0-lite-user-pepper", user_id.encode(), hashlib.sha256).hexdigest()[:24]


def get_or_create_user(db: Session, user_id: str) -> User:
    user = db.get(User, user_id)
    if user is None:
        user = User(id=user_id, external_id=user_id, nickname="演示用户", member_level="silver", points=1200)
        db.add(user)
        db.commit()
    return user


def get_or_create_session(db: Session, user_id: str, session_id: str | None, title: str | None = None) -> ChatSession:
    if session_id:
        session = db.get(ChatSession, session_id)
        if session and session.user_id == user_id:
            return session
    session = ChatSession(
        id=f"s_{secrets.token_hex(8)}",
        user_id=user_id,
        title=(title or "新对话")[:80],
    )
    db.add(session)
    db.commit()
    return session


def recent_messages(db: Session, session_id: str, limit: int = 12, *, include_empty: bool = True) -> list[Message]:
    statement = select(Message).where(Message.session_id == session_id)
    if not include_empty:
        statement = statement.where(
            Message.content != "",
            Message.status.notin_(("streaming", "error")),
        )
    rows = db.scalars(
        statement.order_by(Message.created_at.desc(), Message.id.desc()).limit(limit)
    ).all()
    return list(reversed(rows))


def estimate_tokens(text: str) -> int:
    """Conservative estimate for mixed Chinese/ASCII customer-service text."""
    value = text or ""
    wide = sum(1 for char in value if ord(char) > 127)
    narrow = len(value) - wide
    return wide + (narrow + 3) // 4


def conversation_context(
    db: Session,
    session_id: str,
    *,
    max_turns: int | None = None,
    max_messages: int | None = None,
    max_chars: int | None = None,
    max_tokens: int | None = None,
    per_message_chars: int | None = None,
) -> list[dict[str, str]]:
    """Return a bounded, chronological window without empty assistant placeholders."""
    max_turns = max_turns or settings.context_max_turns
    max_messages = max_messages or settings.context_max_messages
    max_chars = max_chars or settings.context_max_chars
    max_tokens = max_tokens or settings.context_max_tokens
    per_message_chars = per_message_chars or settings.context_per_message_chars
    rows = db.scalars(
        select(Message)
        .where(
            Message.session_id == session_id,
            Message.content != "",
            Message.status.notin_(("streaming", "error")),
        )
        .order_by(Message.created_at.desc(), Message.id.desc())
        .limit(max_messages * 4)
    ).all()
    selected: list[dict[str, str]] = []
    turns: list[str] = []
    used_chars = 0
    used_tokens = 0
    for row in rows:
        content = (row.content or "").strip()
        if not content:
            continue
        if len(content) > per_message_chars:
            content = content[:per_message_chars].rstrip() + "…"
        content_tokens = estimate_tokens(content)
        new_turn = row.turn_id not in turns
        if selected and (
            len(selected) >= max_messages
            or used_chars + len(content) > max_chars
            or used_tokens + content_tokens > max_tokens
            or (new_turn and len(turns) >= max_turns)
        ):
            break
        selected.append({"role": row.role, "content": content})
        used_chars += len(content)
        used_tokens += content_tokens
        if new_turn:
            turns.append(row.turn_id)
    return list(reversed(selected))


def recent_orders(db: Session, user_id: str, limit: int = 5) -> list[Order]:
    return list(
        db.scalars(select(Order).where(Order.user_id == user_id).order_by(Order.created_at.desc()).limit(limit)).all()
    )


def order_summary(order: Order) -> dict:
    return {
        "order_id": order.id,
        "order_no": order.order_no,
        "title": order.title,
        "status": order.status,
        "paid_amount": round(order.paid_amount, 2),
        "delivered_at": order.delivered_at.isoformat() if order.delivered_at else None,
    }


def _shipment_events(shipment) -> list[dict]:
    if shipment is None:
        return []
    try:
        raw = json.loads(shipment.events_json or "[]")
    except Exception:
        return []
    seen = set()
    events = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        event = {"time": str(item.get("time", "")), "text": str(item.get("text", ""))}
        key = (event["time"], event["text"])
        if key in seen:
            continue
        seen.add(key)
        events.append(event)
    events.sort(key=lambda e: (e["time"], e["text"]))
    return events


def _event_time(events: list[dict], *keywords: str) -> str | None:
    for event in events:
        if any(keyword in event["text"] for keyword in keywords):
            return event["time"] or None
    return None


def _build_progress(order: Order, shipment, events: list[dict]) -> dict:
    texts = [event["text"] for event in events]

    def has(*keywords: str) -> bool:
        return any(any(keyword in text for keyword in keywords) for text in texts)

    paid = order.status != "pending_payment" and float(order.paid_amount or 0) > 0
    packed = order.status in {"shipped", "delivered", "refunded"} or has("发货", "出库")
    picked_up = has("揽收")
    in_transit = has("运输", "转运", "到达") or (shipment is not None and shipment.status == "运输中")
    out_for_delivery = has("派送")
    delivered = bool(order.delivered_at) or order.status in {"delivered", "refunded"} or has("签收")

    flags = [True, paid, packed, picked_up, in_transit, out_for_delivery, delivered]
    # 后续节点完成时，前序节点必然已完成（例如“已签收”意味着已揽收）。
    for index in range(len(flags) - 1, 0, -1):
        if flags[index]:
            flags[index - 1] = True

    times = {"placed": order.created_at.strftime("%Y-%m-%d %H:%M") if order.created_at else None}
    times["packed"] = _event_time(events, "发货", "出库")
    times["picked_up"] = _event_time(events, "揽收")
    times["in_transit"] = _event_time(events, "运输", "转运", "到达")
    times["out_for_delivery"] = _event_time(events, "派送")
    signed_time = _event_time(events, "签收") or (
        order.delivered_at.strftime("%Y-%m-%d %H:%M") if order.delivered_at else None
    )
    times["delivered"] = signed_time

    first_pending = next((index for index, done in enumerate(flags) if not done), None)
    last_done = max((index for index, done in enumerate(flags) if done), default=0)
    all_done = first_pending is None
    steps = []
    for index, (key, label) in enumerate(PROGRESS_STEPS):
        if all_done:
            state = "done"
        elif index < last_done:
            state = "done"
        elif index == last_done:
            state = "current"
        else:
            state = "pending"
        step = {"key": key, "label": label, "state": state}
        if times.get(key):
            step["time"] = times[key]
        steps.append(step)

    hint = PROGRESS_HINT
    if order.status == "cancelled":
        hint = "订单已取消，以下为原预计流程，实际以订单状态为准"
    elif order.status == "refunded":
        hint = "订单已退款，以下为签收后的历史流程"
    return {"steps": steps, "hint": hint, "exception": order.status if order.status in {"cancelled", "refunded"} else None}


def logistics_summary(order: Order) -> dict:
    shipment = order.shipment
    events = _shipment_events(shipment)
    has_signed = any("签收" in event["text"] for event in events)
    if not shipment:
        status = {
            "delivered": "已签收",
            "refunded": "已签收",
            "paid": "待发货",
            "pending_payment": "待支付",
            "cancelled": "已取消",
        }.get(order.status, "暂无物流信息")
        return {
            "order_id": order.id,
            "status": status,
            "carrier": None,
            "tracking_no": None,
            "events": [],
            "history_complete": False,
            "progress": _build_progress(order, None, []),
        }
    history_complete = has_signed if (order.delivered_at or order.status in {"delivered", "refunded"}) else bool(events)
    return {
        "order_id": order.id,
        "carrier": shipment.carrier,
        "tracking_no": shipment.tracking_no,
        "status": shipment.status,
        "events": events,
        "history_complete": history_complete,
        "progress": _build_progress(order, shipment, events),
    }


def create_ticket(db: Session, session_id: str, user_id: str, ticket_type: str, context: dict) -> Ticket:
    ticket = Ticket(
        id=f"tk_{secrets.token_hex(6)}",
        session_id=session_id,
        user_id=user_id,
        type=ticket_type,
        context_json=dumps(context),
    )
    db.add(ticket)
    db.commit()
    return ticket


def sign_confirm_token(
    db: Session,
    *,
    user_id: str,
    session_id: str,
    order_id: str,
    reason_code: str,
    amount: float,
    policy_hash: str,
) -> tuple[str, str]:
    jti = f"ct_{secrets.token_hex(8)}"
    expires = utcnow() + timedelta(seconds=settings.confirm_token_ttl_seconds)
    db.add(
        ConfirmToken(
            jti=jti,
            user_id=user_id,
            session_id=session_id,
            order_id=order_id,
            action="refund",
            reason_code=reason_code,
            amount=round(amount, 2),
            policy_hash=policy_hash,
            expires_at=expires,
        )
    )
    db.commit()
    payload = {
        "jti": jti,
        "user_id": user_id,
        "session_id": session_id,
        "order_id": order_id,
        "reason_code": reason_code,
        "amount": round(amount, 2),
        "policy_hash": policy_hash,
        "exp": int(expires.timestamp()),
    }
    raw = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode()
    sig = hmac.new(CONFIRM_SECRET, raw.encode(), hashlib.sha256).hexdigest()
    return f"{raw}.{sig}", jti


def verify_confirm_token(db: Session, token: str, user_id: str, session_id: str, order_id: str, amount: float) -> bool:
    try:
        raw, sig = token.split(".", 1)
        expected = hmac.new(CONFIRM_SECRET, raw.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return False
        payload = json.loads(base64.urlsafe_b64decode(raw.encode()).decode())
        if payload.get("user_id") != user_id or payload.get("session_id") != session_id:
            return False
        if payload.get("order_id") != order_id or abs(float(payload.get("amount", -1)) - round(amount, 2)) > 0.001:
            return False
        row = db.get(ConfirmToken, payload["jti"])
        if row is None or row.used_at is not None or row.expires_at < utcnow():
            return False
        row.used_at = utcnow()
        db.commit()
        return True
    except Exception:
        return False


def create_refund(db: Session, order: Order, decision: RefundDecision, token: str, user_id: str, session_id: str) -> Refund:
    if not verify_confirm_token(db, token, user_id, session_id, order.id, decision.amount):
        raise ValueError("CONFIRM_INVALID")
    existing = db.scalar(select(Refund).where(Refund.order_id == order.id, Refund.active_flag == 1))
    if existing:
        return existing
    refund = Refund(
        id=f"rf_{secrets.token_hex(6)}",
        order_id=order.id,
        user_id=user_id,
        amount=decision.amount,
        reason_code=decision.reason_code,
        status="approved",
        active_flag=1,
        policy_snapshot_json=dumps(decision.policy_snapshot),
        idempotency_key=f"{user_id}:{order.id}:{secrets.token_hex(6)}",
    )
    db.add(refund)
    order.status = "refunding"
    db.commit()
    return refund


def add_feedback(db: Session, message_id: str, user_id: str, rating: str, reason: str | None, comment: str | None) -> Feedback:
    existing = db.scalar(select(Feedback).where(Feedback.message_id == message_id, Feedback.user_id == user_id))
    if existing:
        existing.rating = rating
        existing.reason = reason
        existing.comment = comment
        db.commit()
        return existing
    row = Feedback(message_id=message_id, user_id=user_id, rating=rating, reason=reason, comment=comment)
    db.add(row)
    db.commit()
    return row


def enqueue_question(
    db: Session,
    *,
    question: str,
    session_id: str | None,
    message_id: str | None,
    user_id: str,
    source_type: str,
) -> QuestionEvent | None:
    normalized = re.sub(r"\s+", "", pii_scrub(question or ""))[:500]
    if not normalized:
        return None
    user_hash = hash_user(user_id)
    today = utcnow().date().isoformat()
    exists = db.scalar(
        select(QuestionEvent).where(
            QuestionEvent.normalized == normalized,
            QuestionEvent.user_hash == user_hash,
            QuestionEvent.created_at >= utcnow().replace(hour=0, minute=0, second=0, microsecond=0),
        )
    )
    if exists:
        return exists
    row = QuestionEvent(
        question=pii_scrub(question),
        normalized=normalized,
        session_id=session_id,
        message_id=message_id,
        user_hash=user_hash,
        source_type=source_type,
    )
    db.add(row)
    db.commit()
    return row
