from __future__ import annotations

from datetime import timedelta

from fastapi.testclient import TestClient

from app import graph as graph_module
from app import llm
from app.db import SessionLocal, utcnow
from app.main import app
from app.models import ChatSession, Message
from app.services import conversation_context, estimate_tokens
from app.session_state import load_state
from tests.test_app import chat, parse_sse


def resume_order(client: TestClient, events: list[dict], order_id: str) -> list[dict]:
    interrupt = next(e["data"] for e in events if e["event"] == "interrupt")
    with client.stream(
        "POST",
        "/api/v1/chat/resume",
        headers={"X-User-Id": "u_demo"},
        json={
            "session_id": events[0]["data"]["session_id"],
            "interrupt_id": interrupt["interrupt_id"],
            "response": {"order_id": order_id},
        },
    ) as response:
        assert response.status_code == 200, response.text
        return parse_sse("".join(response.iter_text()))


def answer_text(events: list[dict]) -> str:
    return "".join(e["data"].get("delta", "") for e in events if e["event"] == "token")


def test_refund_reason_answer_continues_instead_of_out_of_scope():
    with TestClient(app) as client:
        events = chat(client, "我要退款", "t-cont-refund-1")
        session_id = events[0]["data"]["session_id"]
        interrupt = next(e["data"] for e in events if e["event"] == "interrupt")
        order_id = interrupt["options"][0]["order_id"]
        resumed = resume_order(client, events, order_id)
        assert "质量问题" in answer_text(resumed)

        follow_up = chat(client, "质量问题", "t-cont-refund-2", session_id=session_id)
        done = next((e["data"] for e in follow_up if e["event"] == "done"), None)
        assert done is None or done.get("reason_code") != "out_of_scope"
        assert "超出我当前能直接办理的范围" not in answer_text(follow_up)
        with SessionLocal() as db:
            session = db.get(ChatSession, session_id)
            state = load_state(session.state_json)
        assert state["pending"] is None
        assert state["facts"]["reason_code"] == "quality"


def test_pending_reason_does_not_hijack_a_new_topic():
    with TestClient(app) as client:
        events = chat(client, "我要退款", "t-cont-switch-1")
        session_id = events[0]["data"]["session_id"]
        interrupt = next(e["data"] for e in events if e["event"] == "interrupt")
        order_id = interrupt["options"][0]["order_id"]
        resume_order(client, events, order_id)

        follow_up = chat(client, "查物流 1001", "t-cont-switch-2", session_id=session_id)
        text = answer_text(follow_up)
        assert "物流" in text
        assert "哪种情况" not in text


def test_reason_question_hands_off_after_two_attempts():
    with TestClient(app) as client:
        events = chat(client, "我要退款", "t-cont-repeat-1")
        session_id = events[0]["data"]["session_id"]
        interrupt = next(e["data"] for e in events if e["event"] == "interrupt")
        order_id = interrupt["options"][0]["order_id"]
        resume_order(client, events, order_id)

        second = chat(client, "不知道", "t-cont-repeat-2", session_id=session_id)
        assert "哪种情况" in answer_text(second)
        third = chat(client, "不知道", "t-cont-repeat-3", session_id=session_id)
        assert "哪种情况" not in answer_text(third)
        done = next(e["data"] for e in third if e["event"] == "done")
        assert done["status"] == "handoff"


def test_context_filters_empty_placeholders_and_respects_message_budget():
    session_id = "s_context_budget"
    with SessionLocal() as db:
        db.add(ChatSession(id=session_id, user_id="u_demo", title="context"))
        base = utcnow()
        for index in range(30):
            db.add(
                Message(
                    id=f"m_ctx_{index:03d}",
                    session_id=session_id,
                    user_id="u_demo",
                    turn_id=f"t_ctx_{index // 2}",
                    role="user" if index % 2 == 0 else "assistant",
                    content="" if index == 29 else f"message-{index}",
                    status="streaming" if index == 29 else "done",
                    created_at=base + timedelta(seconds=index),
                )
            )
        db.commit()
        context = conversation_context(db, session_id, max_turns=12, max_messages=24, max_chars=6000)
    assert context
    assert all(item["content"] for item in context)
    assert len(context) <= 24
    assert "message-29" not in [item["content"] for item in context]


def test_context_respects_token_budget():
    session_id = "s_context_tokens"
    with SessionLocal() as db:
        db.add(ChatSession(id=session_id, user_id="u_demo", title="tokens"))
        base = utcnow()
        for index in range(6):
            db.add(
                Message(
                    id=f"m_tok_{index:03d}",
                    session_id=session_id,
                    user_id="u_demo",
                    turn_id=f"t_tok_{index}",
                    role="user",
                    content="质量问题" * 10,
                    status="done",
                    created_at=base + timedelta(seconds=index),
                )
            )
        db.commit()
        context = conversation_context(
            db,
            session_id,
            max_turns=24,
            max_messages=48,
            max_chars=10000,
            max_tokens=100,
        )
    assert context
    assert sum(estimate_tokens(item["content"]) for item in context) <= 100
    assert len(context) <= 2


def test_estimate_tokens_is_conservative_for_chinese_and_ascii():
    assert estimate_tokens("质量问题") == 4
    assert estimate_tokens("abcd") == 1


def test_healthz_reports_effective_llm_mode_without_exposing_key():
    with TestClient(app) as client:
        data = client.get("/healthz").json()
    assert data["llm_requested"] == "mock"
    assert data["llm_mode"] == "mock"
    assert data["llm_configured"] is False
    assert "deepseek_api_key" not in data


def test_llm_rewrite_is_used_for_short_ambiguous_followups(monkeypatch):
    monkeypatch.setattr(llm, "is_configured", lambda: True)
    monkeypatch.setattr(llm, "rewrite_followup", lambda *args, **kwargs: "订单 1001 质量问题退款")
    result = graph_module.understand(
        {
            "query": "质量问题",
            "history": [
                {"role": "assistant", "content": "请问是哪种情况"},
                {"role": "user", "content": "订单 1001"},
            ],
            "session_state": {},
        }
    )
    assert result["intent"] == "refund_request"
    assert result["rewritten"] == "订单 1001 质量问题退款"
