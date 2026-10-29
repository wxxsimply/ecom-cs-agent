from __future__ import annotations

import json
import re

from fastapi.testclient import TestClient

from app.main import app


def parse_sse(text: str) -> list[dict]:
    events = []
    for frame in re.split(r"\r?\n\r?\n", text):
        if not frame.strip():
            continue
        event = "message"
        data_lines = []
        for line in frame.splitlines():
            if line.startswith("event:"):
                event = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                data_lines.append(line.split(":", 1)[1].strip())
        if data_lines:
            events.append({"event": event, "data": json.loads("\n".join(data_lines))})
    return events


def chat(client: TestClient, message: str, cid: str, session_id: str | None = None):
    with client.stream(
        "POST",
        "/api/v1/chat/stream",
        headers={"X-User-Id": "u_demo"},
        json={"session_id": session_id, "message": message, "client_message_id": cid, "context": {"page": "chat"}},
    ) as response:
        assert response.status_code == 200, response.text
        text = "".join(response.iter_text())
    return parse_sse(text)


def test_health_and_seed():
    with TestClient(app) as client:
        response = client.get("/healthz")
        assert response.status_code == 200
        assert response.json()["ok"] is True
        orders = client.get("/api/v1/orders", headers={"X-User-Id": "u_demo"}).json()["items"]
        assert {o["order_id"] for o in orders} >= {"1001", "1002"}


def test_shipping_answer_has_citations():
    with TestClient(app) as client:
        events = chat(client, "邮费是多少", "t-shipping-1")
        types = [e["event"] for e in events]
        assert types[0] == "meta"
        assert "citation" in types and "token" in types and "done" in types
        assert types.index("citation") < types.index("token")
        answer = "".join(e["data"].get("delta", "") for e in events if e["event"] == "token")
        assert "99" in answer and "6" in answer and "12" in answer
        done = next(e["data"] for e in events if e["event"] == "done")
        assert done["citations_used"]


def test_order_query_is_scoped_to_user():
    with TestClient(app) as client:
        response = client.get("/api/v1/orders/1001", headers={"X-User-Id": "other"})
        assert response.status_code == 404


def test_refund_requires_confirm_and_completes():
    with TestClient(app) as client:
        events = chat(client, "我要退款，质量问题，订单 1001", "t-refund-1")
        interrupt = next(e["data"] for e in events if e["event"] == "interrupt")
        assert interrupt["type"] == "confirm_refund"
        assert interrupt["amount"] > 0
        with client.stream(
            "POST",
            "/api/v1/chat/resume",
            headers={"X-User-Id": "u_demo"},
            json={
                "session_id": events[0]["data"]["session_id"],
                "interrupt_id": interrupt["interrupt_id"],
                "response": {"confirmed": True},
                "confirm_token": interrupt["confirm_token"],
            },
        ) as response:
            resumed = parse_sse("".join(response.iter_text()))
        assert any(e["event"] == "done" for e in resumed)
        done = next(e["data"] for e in resumed if e["event"] == "done")
        assert done["status"] == "action_done"
        assert done["receipt"]["amount"] == interrupt["amount"]


def test_order_select_interrupt_and_resume():
    with TestClient(app) as client:
        events = chat(client, "我要退款", "t-order-1")
        interrupt = next(e["data"] for e in events if e["event"] == "interrupt")
        assert interrupt["type"] == "order_select"
        assert interrupt["options"]
        with client.stream(
            "POST",
            "/api/v1/chat/resume",
            headers={"X-User-Id": "u_demo"},
            json={
                "session_id": events[0]["data"]["session_id"],
                "interrupt_id": interrupt["interrupt_id"],
                "response": {"order_id": interrupt["options"][0]["order_id"]},
            },
        ) as response:
            resumed = parse_sse("".join(response.iter_text()))
        assert any(e["event"] in {"interrupt", "done"} for e in resumed)


def test_feedback_and_session_history():
    with TestClient(app) as client:
        events = chat(client, "你好", "t-feedback-1")
        session_id = events[0]["data"]["session_id"]
        done = next(e["data"] for e in events if e["event"] == "done")
        # assistant message id is in meta
        message_id = events[0]["data"]["message_id"]
        response = client.post(
            f"/api/v1/messages/{message_id}/feedback",
            headers={"X-User-Id": "u_demo"},
            json={"rating": "down", "reason": "not_solved"},
        )
        assert response.status_code == 200
        history = client.get(f"/api/v1/sessions/{session_id}/messages", headers={"X-User-Id": "u_demo"})
        assert history.status_code == 200
        assert len(history.json()["items"]) >= 2


def test_pii_is_scrubbed_in_history():
    with TestClient(app) as client:
        events = chat(client, "我的手机号是13812345678，邮费是多少", "t-pii-1")
        session_id = events[0]["data"]["session_id"]
        history = client.get(f"/api/v1/sessions/{session_id}/messages", headers={"X-User-Id": "u_demo"}).json()
        user_text = next(m["content"] for m in history["items"] if m["role"] == "user")
        assert "13812345678" not in user_text
        assert "138****0000" in user_text
