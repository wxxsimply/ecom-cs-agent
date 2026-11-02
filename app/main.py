from __future__ import annotations

import json
import secrets
import time
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.after_sales_agent import plan_ticket
from app.chat import get_pending_interrupt, resume_turn, run_turn
from app.config import settings
from app.db import SessionLocal, get_db, init_db, loads, utcnow
from app.models import ChatSession, Feedback, Message, Order, Refund
from app.services import add_feedback, get_or_create_session, get_or_create_user, order_summary, recent_messages, recent_orders, enqueue_question, pii_scrub

BASE_DIR = Path(__file__).resolve().parent.parent
WEB_DIR = BASE_DIR / "web"

app = FastAPI(title="MewHelp P0-Lite", version="0.1.0")


class ChatRequest(BaseModel):
    session_id: str | None = None
    message: str = Field(min_length=1, max_length=settings.max_input_chars)
    client_message_id: str = Field(min_length=1, max_length=64)
    context: dict = Field(default_factory=dict)


class ResumeRequest(BaseModel):
    session_id: str
    interrupt_id: str
    response: dict = Field(default_factory=dict)
    confirm_token: str | None = None


class FeedbackRequest(BaseModel):
    rating: str
    reason: str | None = None
    comment: str | None = None


class AfterSalesPlanRequest(BaseModel):
    message: str = Field(min_length=1, max_length=settings.max_input_chars)
    order_no: str | None = None
    facts: dict = Field(default_factory=dict)


def sse(event: str, data: dict, seq: int, turn_id: str) -> str:
    return f"id: {turn_id}:{seq}\nevent: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


def _citations(message: Message) -> list[dict]:
    return loads(message.citations_json, []) or []


def _user_id(x_user_id: str | None) -> str:
    # P0-Lite: 本地演示身份；生产必须 JWT。见 CONTEXT.md。
    return x_user_id or settings.demo_user_id


def _session_or_error(db: Session, session_id: str, user_id: str) -> ChatSession:
    session = db.get(ChatSession, session_id)
    if session is None or session.user_id != user_id:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "会话不存在或已失效"})
    return session


def _owned_message(db: Session, message_id: str, user_id: str) -> Message:
    message = db.get(Message, message_id)
    if message is None or message.user_id != user_id:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "消息不存在"})
    return message


def _stream_message(turn_id: str, message: Message, resumed: bool = False, receipt: dict | None = None):
    seq = 0
    seq += 1
    yield sse(
        "meta",
        {
            "turn_id": turn_id,
            "session_id": message.session_id,
            "message_id": message.id,
            "trace_id": message.trace_id,
            "resumed": resumed,
        },
        seq,
        turn_id,
    )
    citations = _citations(message)
    for citation in citations:
        seq += 1
        yield sse("citation", citation, seq, turn_id)
    if receipt:
        seq += 1
        yield sse("phase", {"phase": "write"}, seq, turn_id)
    content = message.content or ""
    # P0 流式是“已校验句子”的逐块下发；不是原始 LLM token。
    for i in range(0, len(content), 4):
        seq += 1
        yield sse("token", {"delta": content[i : i + 4]}, seq, turn_id)
    seq += 1
    status = message.status if message.status in {"ok", "refused", "handoff", "action_done", "cancelled"} else "ok"
    yield sse(
        "done",
        {
            "status": status,
            "answer_status": status,
            "citations_used": [c.get("index") for c in citations],
            "receipt": receipt,
            "progress": loads(message.progress_json, None),
            "usage": None,
            "latency_ms": 0,
        },
        seq,
        turn_id,
    )


def _stream_interrupt(turn_id: str, message: Message, payload: dict):
    seq = 0
    seq += 1
    yield sse(
        "meta",
        {
            "turn_id": turn_id,
            "session_id": message.session_id,
            "message_id": message.id,
            "trace_id": message.trace_id,
            "resumed": False,
        },
        seq,
        turn_id,
    )
    seq += 1
    yield sse("interrupt", payload, seq, turn_id)


@app.on_event("startup")
def startup() -> None:
    init_db()
    try:
        from scripts.seed import seed

        seed()
    except Exception:
        # 种子失败不应阻止 /healthz；开发时可见日志。
        pass


@app.get("/healthz")
def healthz():
    return {"ok": True, "app": settings.app_name, "llm_mode": settings.llm_mode}


@app.get("/")
def index():
    path = WEB_DIR / "index.html"
    if path.exists():
        return FileResponse(path)
    return JSONResponse({"message": "web/index.html not found"}, status_code=404)


@app.get("/app.js")
def app_js():
    return FileResponse(WEB_DIR / "app.js", media_type="application/javascript")


@app.get("/styles.css")
def styles_css():
    return FileResponse(WEB_DIR / "styles.css", media_type="text/css")


if WEB_DIR.exists():
    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")


@app.post("/api/v1/chat/stream")
def chat_stream(req: ChatRequest, x_user_id: str | None = Header(default=None, alias="X-User-Id")):
    user_id = _user_id(x_user_id)
    with SessionLocal() as db:
        get_or_create_user(db, user_id)
        existing = db.scalar(select(Message).where(Message.user_id == user_id, Message.client_message_id == req.client_message_id))
        if existing:
            assistant = db.scalar(
                select(Message).where(
                    Message.session_id == existing.session_id,
                    Message.turn_id == existing.turn_id,
                    Message.role == "assistant",
                )
            )
            if assistant is None:
                raise HTTPException(status_code=409, detail={"code": "TURN_IN_PROGRESS", "message": "本轮仍在处理中"})
            if assistant.status == "waiting_user":
                payload = get_pending_interrupt(assistant.session_id)
                return StreamingResponse(
                    _stream_interrupt(assistant.turn_id, assistant, payload or {}),
                    media_type="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
                )
            return StreamingResponse(
                _stream_message(assistant.turn_id, assistant),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )
        if req.session_id:
            existing_session = db.get(ChatSession, req.session_id)
            if existing_session is None or existing_session.user_id != user_id:
                raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "会话不存在或已失效"})
        clean_message = pii_scrub(req.message)
        session = get_or_create_session(db, user_id, req.session_id, clean_message[:30])
        turn_id = req.client_message_id
        user_msg = Message(
            id=f"m_{secrets.token_hex(8)}",
            session_id=session.id,
            user_id=user_id,
            turn_id=turn_id,
            client_message_id=req.client_message_id,
            role="user",
            content=clean_message,
            status="done",
        )
        assistant = Message(
            id=f"m_{secrets.token_hex(8)}",
            session_id=session.id,
            user_id=user_id,
            turn_id=turn_id,
            client_message_id=None,
            role="assistant",
            content="",
            status="streaming",
            progress_json="[]",
            trace_id=f"tr_{secrets.token_hex(6)}",
        )
        db.add_all([user_msg, assistant])
        session.updated_at = utcnow()
        if session.title == "新对话":
            session.title = req.message[:30]
        db.commit()
        db.refresh(session)
        db.refresh(assistant)

        def generate():
            # meta 先发；然后再跑图，避免长链路没有任何事件。
            seq = 0
            seq += 1
            yield sse(
                "meta",
                {
                    "turn_id": turn_id,
                    "session_id": session.id,
                    "message_id": assistant.id,
                    "trace_id": assistant.trace_id,
                    "resumed": False,
                },
                seq,
                turn_id,
            )
            try:
                result = run_turn(session, user_id, clean_message, turn_id)
                with SessionLocal() as db:
                    msg = db.get(Message, assistant.id)
                    if result.status == "interrupt":
                        msg.status = "waiting_user"
                        msg.content = ""
                        db.commit()
                        seq += 1
                        yield sse("interrupt", result.interrupt or {}, seq, turn_id)
                        return
                    if result.status in {"refused", "handoff"}:
                        enqueue_question(
                            db,
                            question=clean_message,
                            session_id=session.id,
                            message_id=assistant.id,
                            user_id=user_id,
                            source_type="refusal" if result.status == "refused" else "handoff",
                        )
                    msg.content = result.answer
                    msg.status = result.status
                    msg.citations_json = json.dumps(result.citations or [], ensure_ascii=False)
                    msg.progress_json = json.dumps(result.progress, ensure_ascii=False) if result.progress else "[]"
                    db.commit()
                    db.refresh(msg)
                if result.citations:
                    for citation in result.citations:
                        seq += 1
                        yield sse("citation", citation, seq, turn_id)
                if result.receipt:
                    seq += 1
                    yield sse("phase", {"phase": "write"}, seq, turn_id)
                for i in range(0, len(result.answer), 4):
                    seq += 1
                    yield sse("token", {"delta": result.answer[i : i + 4]}, seq, turn_id)
                seq += 1
                yield sse(
                    "done",
                    {
                        "status": result.status,
                        "answer_status": result.status,
                        "citations_used": [c.get("index") for c in (result.citations or [])],
                        "receipt": result.receipt,
                        "progress": result.progress,
                        "reason_code": result.refusal_code,
                        "usage": None,
                        "latency_ms": 0,
                    },
                    seq,
                    turn_id,
                )
            except Exception as exc:
                with SessionLocal() as db:
                    msg = db.get(Message, assistant.id)
                    if msg:
                        msg.status = "error"
                        db.commit()
                seq += 1
                yield sse("error", {"code": "INTERNAL_ERROR", "recoverable": True, "message": str(exc)[:200]}, seq, turn_id)

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )


@app.post("/api/v1/chat/resume")
def chat_resume(req: ResumeRequest, x_user_id: str | None = Header(default=None, alias="X-User-Id")):
    user_id = _user_id(x_user_id)
    with SessionLocal() as db:
        session = _session_or_error(db, req.session_id, user_id)
        existing = db.scalar(select(Message).where(Message.session_id == session.id, Message.role == "assistant").order_by(Message.created_at.desc()))
        if existing is None:
            raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "消息不存在"})
        turn_id = existing.turn_id

        def generate():
            seq = 0
            seq += 1
            yield sse(
                "meta",
                {"turn_id": turn_id, "session_id": session.id, "message_id": existing.id, "trace_id": existing.trace_id, "resumed": True},
                seq,
                turn_id,
            )
            try:
                result = resume_turn(session, user_id, req.interrupt_id, req.response, req.confirm_token)
                with SessionLocal() as db2:
                    msg = db2.get(Message, existing.id)
                    if result.status == "interrupt":
                        msg.status = "waiting_user"
                        msg.content = ""
                        db2.commit()
                        seq += 1
                        yield sse("interrupt", result.interrupt or {}, seq, turn_id)
                        return
                    msg.content = result.answer
                    msg.status = result.status
                    msg.citations_json = json.dumps(result.citations or [], ensure_ascii=False)
                    msg.progress_json = json.dumps(result.progress, ensure_ascii=False) if result.progress else "[]"
                    db2.commit()
                    db2.refresh(msg)
                for citation in result.citations or []:
                    seq += 1
                    yield sse("citation", citation, seq, turn_id)
                if result.receipt:
                    seq += 1
                    yield sse("phase", {"phase": "write"}, seq, turn_id)
                for i in range(0, len(result.answer), 4):
                    seq += 1
                    yield sse("token", {"delta": result.answer[i : i + 4]}, seq, turn_id)
                seq += 1
                yield sse(
                    "done",
                    {
                        "status": result.status,
                        "answer_status": result.status,
                        "citations_used": [c.get("index") for c in (result.citations or [])],
                        "receipt": result.receipt,
                        "progress": result.progress,
                        "reason_code": result.refusal_code,
                        "usage": None,
                        "latency_ms": 0,
                    },
                    seq,
                    turn_id,
                )
            except ValueError as exc:
                seq += 1
                code = str(exc) if str(exc) in {"INTERRUPT_EXPIRED"} else "INTERRUPT_EXPIRED"
                yield sse("error", {"code": code, "recoverable": False, "message": "中断已过期，请重新发起"}, seq, turn_id)
            except Exception as exc:
                seq += 1
                yield sse("error", {"code": "INTERNAL_ERROR", "recoverable": True, "message": str(exc)[:200]}, seq, turn_id)

        return StreamingResponse(generate(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/v1/sessions")
def list_sessions(limit: int = 20, x_user_id: str | None = Header(default=None, alias="X-User-Id")):
    user_id = _user_id(x_user_id)
    with SessionLocal() as db:
        rows = db.scalars(select(ChatSession).where(ChatSession.user_id == user_id).order_by(ChatSession.updated_at.desc()).limit(limit)).all()
        return {"items": [{"id": s.id, "title": s.title, "updated_at": s.updated_at.isoformat()} for s in rows]}


@app.get("/api/v1/sessions/{session_id}/messages")
def list_messages(session_id: str, x_user_id: str | None = Header(default=None, alias="X-User-Id")):
    user_id = _user_id(x_user_id)
    with SessionLocal() as db:
        session = _session_or_error(db, session_id, user_id)
        rows = recent_messages(db, session.id, limit=50)
        return {
            "items": [
                {
                    "id": m.id,
                    "role": m.role,
                    "content": m.content,
                    "citations": _citations(m),
                    "progress": loads(m.progress_json, None),
                    "status": m.status,
                    "created_at": m.created_at.isoformat(),
                }
                for m in rows
            ]
        }


@app.get("/api/v1/sessions/{session_id}/interrupt")
def get_interrupt(session_id: str, x_user_id: str | None = Header(default=None, alias="X-User-Id")):
    user_id = _user_id(x_user_id)
    with SessionLocal() as db:
        _session_or_error(db, session_id, user_id)
    return {"interrupt": get_pending_interrupt(session_id)}


@app.post("/api/v1/messages/{message_id}/feedback")
def feedback(message_id: str, req: FeedbackRequest, x_user_id: str | None = Header(default=None, alias="X-User-Id")):
    user_id = _user_id(x_user_id)
    if req.rating not in {"up", "down"}:
        raise HTTPException(status_code=422, detail={"code": "VALIDATION_ERROR", "message": "rating 必须是 up/down"})
    with SessionLocal() as db:
        _owned_message(db, message_id, user_id)
        row = add_feedback(db, message_id, user_id, req.rating, req.reason, pii_scrub(req.comment) if req.comment else None)
        if req.rating == "down":
            message = db.get(Message, message_id)
            enqueue_question(
                db,
                question=message.content if message else "用户点踩",
                session_id=message.session_id if message else None,
                message_id=message_id,
                user_id=user_id,
                source_type="feedback",
            )
        return {"ok": True, "id": row.id}


@app.post("/api/v1/after-sales/plan")
def after_sales_plan(req: AfterSalesPlanRequest):
    """P1 minimal slice: deterministic ticket decision; no execution/approval persistence yet."""
    return {"plan": plan_ticket(req.model_dump()).to_dict()}


@app.get("/api/v1/orders")
def my_orders(x_user_id: str | None = Header(default=None, alias="X-User-Id")):
    user_id = _user_id(x_user_id)
    with SessionLocal() as db:
        return {"items": [order_summary(o) for o in recent_orders(db, user_id, limit=20)]}


@app.get("/api/v1/orders/{order_id}")
def order_detail(order_id: str, x_user_id: str | None = Header(default=None, alias="X-User-Id")):
    user_id = _user_id(x_user_id)
    with SessionLocal() as db:
        order = db.get(Order, order_id)
        if order is None or order.user_id != user_id:
            raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "订单不存在"})
        return order_summary(order)


@app.get("/api/v1/orders/{order_id}/logistics")
def order_logistics(order_id: str, x_user_id: str | None = Header(default=None, alias="X-User-Id")):
    user_id = _user_id(x_user_id)
    with SessionLocal() as db:
        order = db.get(Order, order_id)
        if order is None or order.user_id != user_id:
            raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "订单不存在"})
        from app.services import logistics_summary

        return logistics_summary(order)
