from __future__ import annotations

import json
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import timedelta

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from app.config import settings
from app.db import SessionLocal, loads, utcnow
from app.graph import State, build_graph
from app.models import ChatInterrupt, ChatSession
from app.services import conversation_context, recent_orders, order_summary
from app.session_state import build_summary, clear_pending, dump_state, load_state, record_turn, set_pending

_conn = sqlite3.connect(settings.checkpoint_path, check_same_thread=False)
checkpointer = SqliteSaver(_conn)
graph = build_graph(checkpointer)


@dataclass
class TurnResult:
    status: str
    answer: str = ""
    citations: list[dict] | None = None
    receipt: dict | None = None
    interrupt: dict | None = None
    progress: dict | None = None
    refusal_code: str | None = None
    quality_flag: str = "ok"


def _state_for(db, session: ChatSession, turn_id: str, user_id: str, query: str) -> State:
    history = conversation_context(db, session.id)
    orders = [order_summary(o) for o in recent_orders(db, user_id, limit=5)]
    session_state = load_state(session.state_json)
    return {
        "session_id": session.id,
        "turn_id": turn_id,
        "user_id": user_id,
        "trace_id": f"tr_{secrets.token_hex(6)}",
        "query": query,
        "history": history,
        "summary": session.summary or "",
        "session_state": session_state,
        "pending_slot": session_state.get("pending"),
        "last_order_id": session_state.get("last_order_id"),
        "recent_orders": orders,
        "status": "ok",
    }


def _persist_state(
    session_id: str,
    turn_id: str,
    result: dict,
    *,
    interrupt_payload: dict | None = None,
    answer: str = "",
    history: list[dict] | None = None,
) -> None:
    with SessionLocal() as db:
        session = db.get(ChatSession, session_id)
        if session is None:
            return
        state = load_state(session.state_json)
        record_turn(
            state,
            turn_id=turn_id,
            intent=result.get("intent"),
            order_id=result.get("order_id"),
            reason_code=result.get("reason_code"),
            pending_slot=result.get("pending_slot"),
            pending_cleared=bool(result.get("pending_cleared")),
            asked_question=result.get("asked_question"),
        )
        if interrupt_payload:
            interrupt_type = interrupt_payload.get("type")
            if interrupt_type == "order_select":
                set_pending(state, "order_select", field="order_id", turn_id=turn_id, attempts=1)
            elif interrupt_type == "confirm_refund":
                clear_pending(state)
                if interrupt_payload.get("order_id"):
                    state["last_order_id"] = interrupt_payload["order_id"]
        session.state_json = dump_state(state)
        session.updated_at = utcnow()
        turn_count = int(state.get("turn_count", 0))
        trigger = max(1, settings.summary_trigger_turns)
        if answer and (turn_count % trigger == 0 or (not session.summary and turn_count >= 2)):
            messages = list(history or conversation_context(db, session_id))
            messages.append({"role": "assistant", "content": answer})
            session.summary = build_summary(messages, state)[: settings.summary_max_chars]
        db.commit()


def run_turn(session: ChatSession, user_id: str, query: str, turn_id: str) -> TurnResult:
    with SessionLocal() as db:
        state = _state_for(db, session, turn_id, user_id, query)
    config = {"configurable": {"thread_id": f"{session.id}:{turn_id}"}}
    result = graph.invoke(state, config=config)
    interrupts = result.get("__interrupt__") if isinstance(result, dict) else None
    if interrupts:
        value = interrupts[0].value if hasattr(interrupts[0], "value") else interrupts[0]
        payload = dict(value)
        with SessionLocal() as db:
            row = ChatInterrupt(
                id=payload["interrupt_id"],
                session_id=session.id,
                turn_id=turn_id,
                thread_id=f"{session.id}:{turn_id}",
                type=payload.get("type", "order_select"),
                payload_json=json.dumps(payload, ensure_ascii=False),
                status="pending",
                expires_at=utcnow() + timedelta(seconds=settings.interrupt_ttl_seconds),
            )
            db.add(row)
            db.commit()
        _persist_state(session.id, turn_id, result, interrupt_payload=payload)
        return TurnResult(status="interrupt", interrupt=payload)
    _persist_state(session.id, turn_id, result, answer=str(result.get("answer") or ""), history=state.get("history"))
    return TurnResult(
        status=str(result.get("status") or "ok"),
        answer=str(result.get("answer") or ""),
        citations=result.get("citations") or [],
        receipt=result.get("receipt"),
        progress=result.get("progress"),
        refusal_code=result.get("refusal_code"),
        quality_flag=str(result.get("quality_flag") or "ok"),
    )


def resume_turn(session: ChatSession, user_id: str, interrupt_id: str, value: dict, confirm_token: str | None) -> TurnResult:
    with SessionLocal() as db:
        row = db.get(ChatInterrupt, interrupt_id)
        if row is None or row.session_id != session.id or row.status != "pending" or row.expires_at < utcnow():
            raise ValueError("INTERRUPT_EXPIRED")
        db.refresh(row)
        row.status = "resuming"
        db.commit()
        payload = loads(row.payload_json, {})
        thread_id = row.thread_id
        turn_id = row.turn_id
    resume_value = dict(value or {})
    if confirm_token:
        resume_value["confirm_token"] = confirm_token
    config = {"configurable": {"thread_id": thread_id}}
    result = graph.invoke(Command(resume=resume_value), config=config)
    if isinstance(result, dict) and result.get("__interrupt__"):
        interrupts = result["__interrupt__"]
        next_value = interrupts[0].value if hasattr(interrupts[0], "value") else interrupts[0]
        with SessionLocal() as db:
            row = db.get(ChatInterrupt, interrupt_id)
            if row:
                row.status = "done"
            next_payload = dict(next_value)
            db.add(
                ChatInterrupt(
                    id=next_payload["interrupt_id"],
                    session_id=session.id,
                    turn_id=turn_id,
                    thread_id=thread_id,
                    type=next_payload.get("type", "confirm_refund"),
                    payload_json=json.dumps(next_payload, ensure_ascii=False),
                    status="pending",
                    expires_at=utcnow() + timedelta(seconds=settings.interrupt_ttl_seconds),
                )
            )
            db.commit()
        _persist_state(session.id, turn_id, result, interrupt_payload=next_payload)
        return TurnResult(status="interrupt", interrupt=next_payload)
    with SessionLocal() as db:
        row = db.get(ChatInterrupt, interrupt_id)
        if row:
            row.status = "done"
        db.commit()
    _persist_state(
        session.id,
        turn_id,
        result,
        answer=str(result.get("answer") or ""),
    )
    return TurnResult(
        status=str(result.get("status") or "ok"),
        answer=str(result.get("answer") or ""),
        citations=result.get("citations") or [],
        receipt=result.get("receipt"),
        progress=result.get("progress"),
        refusal_code=result.get("refusal_code"),
        quality_flag=str(result.get("quality_flag") or "ok"),
    )


def get_pending_interrupt(session_id: str) -> dict | None:
    with SessionLocal() as db:
        row = db.query(ChatInterrupt).filter(
            ChatInterrupt.session_id == session_id,
            ChatInterrupt.status == "pending",
            ChatInterrupt.expires_at > utcnow(),
        ).order_by(ChatInterrupt.created_at.desc()).first()
        if row is None:
            return None
        return loads(row.payload_json, None)
