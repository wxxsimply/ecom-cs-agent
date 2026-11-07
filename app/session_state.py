from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from app.config import settings
from app.db import utcnow
from app.intent import extract_order_id, extract_reason

STATE_VERSION = 1


def empty_state() -> dict[str, Any]:
    return {
        "version": STATE_VERSION,
        "turn_count": 0,
        "last_turn_id": None,
        "last_order_id": None,
        "last_intent": None,
        "topic": None,
        "pending": None,
        "facts": {},
        "asked_questions": [],
        "updated_at": None,
    }


def load_state(raw: str | dict[str, Any] | None) -> dict[str, Any]:
    if isinstance(raw, dict):
        data = dict(raw)
    else:
        try:
            data = json.loads(raw or "{}")
        except (TypeError, ValueError):
            data = {}
    if not isinstance(data, dict):
        data = {}
    state = empty_state()
    state.update(data)
    state["version"] = STATE_VERSION
    if not isinstance(state.get("facts"), dict):
        state["facts"] = {}
    if not isinstance(state.get("asked_questions"), list):
        state["asked_questions"] = []
    if state.get("pending") is not None and not isinstance(state["pending"], dict):
        state["pending"] = None
    return state


def dump_state(state: dict[str, Any]) -> str:
    return json.dumps(state, ensure_ascii=False, default=str)


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def pending_expired(pending: dict[str, Any] | None) -> bool:
    if not pending:
        return False
    expires = _parse_dt(pending.get("expires_at"))
    return bool(expires and expires < utcnow())


def set_pending(
    state: dict[str, Any],
    pending_type: str,
    *,
    field: str | None = None,
    allowed: list[str] | None = None,
    turn_id: str | None = None,
    attempts: int | None = None,
) -> dict[str, Any]:
    old = state.get("pending") if isinstance(state.get("pending"), dict) else {}
    if attempts is None:
        attempts = int(old.get("attempts", 0)) + 1 if old.get("type") == pending_type else 1
    pending = {
        "type": pending_type,
        "field": field,
        "allowed": allowed or [],
        "asked_at_turn": turn_id,
        "expires_at": (utcnow() + timedelta(seconds=settings.interrupt_ttl_seconds)).isoformat(),
        "attempts": attempts,
    }
    state["pending"] = pending
    state["updated_at"] = utcnow().isoformat()
    return pending


def clear_pending(state: dict[str, Any]) -> None:
    state["pending"] = None
    state["updated_at"] = utcnow().isoformat()


def match_pending(state: dict[str, Any], query: str) -> dict[str, Any] | None:
    pending = state.get("pending")
    if not isinstance(pending, dict) or pending_expired(pending):
        return None
    pending_type = pending.get("type")
    if pending_type == "reason_select":
        raw = extract_reason(query)
        if raw is None and any(token in (query or "") for token in ("其他", "其它")):
            raw = "other"
        allowed = pending.get("allowed") or []
        if raw and (not allowed or raw in allowed):
            return {"field": "reason_code", "value": raw, "pending": pending}
    if pending_type == "order_select":
        order_id = extract_order_id(query)
        if order_id:
            return {"field": "order_id", "value": order_id, "pending": pending}
    return None


def is_new_topic(query: str) -> bool:
    text = query or ""
    if extract_reason(text) is not None:
        return False
    switch_keywords = (
        "物流",
        "快递",
        "到哪",
        "发货",
        "签收",
        "运单",
        "转人工",
        "人工",
        "投诉",
        "邮费",
        "运费",
        "包邮",
        "发票",
        "会员",
        "积分",
    )
    if any(keyword in text for keyword in switch_keywords):
        return True
    if "订单" in text and extract_order_id(text):
        return True
    return len(text.strip()) >= 8 and any(keyword in text for keyword in ("查", "问", "怎么", "多少", "为什么"))


def record_turn(
    state: dict[str, Any],
    *,
    turn_id: str | None = None,
    intent: str | None = None,
    order_id: str | None = None,
    reason_code: str | None = None,
    pending_slot: dict[str, Any] | None = None,
    pending_cleared: bool = False,
    asked_question: str | None = None,
) -> dict[str, Any]:
    same_turn = bool(turn_id and state.get("last_turn_id") == turn_id)
    if not same_turn:
        state["turn_count"] = int(state.get("turn_count", 0)) + 1
        state["last_turn_id"] = turn_id
    state["updated_at"] = utcnow().isoformat()
    if intent:
        state["last_intent"] = intent
        if intent in {"refund_request", "after_sales"}:
            state["topic"] = "refund"
        elif intent in {"order_query", "logistics_query"}:
            state["topic"] = "order"
    if order_id:
        state["last_order_id"] = order_id
    if reason_code:
        facts = state.setdefault("facts", {})
        if not isinstance(facts, dict):
            facts = {}
            state["facts"] = facts
        facts["reason_code"] = reason_code
    if pending_slot:
        set_pending(
            state,
            str(pending_slot.get("type") or "unknown"),
            field=pending_slot.get("field"),
            allowed=list(pending_slot.get("allowed") or []),
            turn_id=turn_id,
            attempts=pending_slot.get("attempts"),
        )
    elif pending_cleared:
        clear_pending(state)
    if asked_question:
        asked = state.setdefault("asked_questions", [])
        if not asked or asked[-1] != asked_question:
            asked.append(asked_question)
            del asked[:-6]
    return state


def build_summary(messages: list[dict[str, str]], state: dict[str, Any], *, max_chars: int | None = None) -> str:
    limit = max_chars or settings.summary_max_chars
    user_messages = [str(m.get("content", "")).strip() for m in messages if m.get("role") == "user"]
    user_messages = [text for text in user_messages if text][-4:]
    order_id = state.get("last_order_id") or ""
    reason = (state.get("facts") or {}).get("reason_code") or ""
    pending = state.get("pending") or {}
    pending_text = ""
    if isinstance(pending, dict) and pending.get("type"):
        pending_text = f"{pending.get('type')}（第 {pending.get('attempts', 1)} 次）"
    lines = ["[summary-v1]"]
    if order_id:
        lines.append(f"订单：{order_id}")
    if reason:
        lines.append(f"当前诉求：退款/售后；原因：{reason}")
    elif state.get("last_intent"):
        lines.append(f"最近意图：{state.get('last_intent')}")
    if pending_text:
        lines.append(f"未完成事项：等待用户回答 {pending_text}")
    if user_messages:
        lines.append("最近用户：" + " / ".join(text[:120] for text in user_messages))
    summary = "\n".join(lines)
    return summary[:limit]
