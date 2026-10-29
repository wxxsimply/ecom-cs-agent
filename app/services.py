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


def recent_messages(db: Session, session_id: str, limit: int = 12) -> list[Message]:
    rows = db.scalars(
        select(Message).where(Message.session_id == session_id).order_by(Message.created_at.desc()).limit(limit)
    ).all()
    return list(reversed(rows))


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


def logistics_summary(order: Order) -> dict:
    shipment = order.shipment
    if not shipment:
        return {"order_id": order.id, "status": "暂无物流信息", "events": []}
    try:
        events = json.loads(shipment.events_json or "[]")
    except Exception:
        events = []
    return {
        "order_id": order.id,
        "carrier": shipment.carrier,
        "tracking_no": shipment.tracking_no,
        "status": shipment.status,
        "events": events[-3:],
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
