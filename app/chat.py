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
from app.models import ChatInterrupt, ChatSession, Message
from app.services import get_or_create_session, recent_messages, recent_orders, order_summary

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
    quality_flag: str = "ok"


def _state_for(db, session: ChatSession, turn_id: str, user_id: str, query: str) -> State:
    history = [{"role": m.role, "content": m.content} for m in recent_messages(db, session.id, limit=12)]
    orders = [order_summary(o) for o in recent_orders(db, user_id, limit=5)]
    return {
        "session_id": session.id,
        "turn_id": turn_id,
        "user_id": user_id,
        "trace_id": f"tr_{secrets.token_hex(6)}",
        "query": query,
        "history": history,
        "summary": session.summary or "",
        "recent_orders": orders,
        "status": "ok",
    }


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
            sess = db.get(ChatSession, session.id)
            if sess:
                sess.state_json = json.dumps({"pending_interrupt_id": payload["interrupt_id"]}, ensure_ascii=False)
                sess.updated_at = utcnow()
            db.commit()
        return TurnResult(status="interrupt", interrupt=payload)
    return TurnResult(
        status=str(result.get("status") or "ok"),
        answer=str(result.get("answer") or ""),
        citations=result.get("citations") or [],
        receipt=result.get("receipt"),
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
        return TurnResult(status="interrupt", interrupt=next_payload)
    with SessionLocal() as db:
        row = db.get(ChatInterrupt, interrupt_id)
        if row:
            row.status = "done"
        db.commit()
    return TurnResult(
        status=str(result.get("status") or "ok"),
        answer=str(result.get("answer") or ""),
        citations=result.get("citations") or [],
        receipt=result.get("receipt"),
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
