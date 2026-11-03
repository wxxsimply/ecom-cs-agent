from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.after_sales_agent import plan_ticket
from app.main import app

ROOT = Path(__file__).resolve().parent.parent
CASES = [json.loads(line) for line in (ROOT / "data" / "eval" / "tickets_v1.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]


def test_ticket_planner_hard_gates_on_synthetic_set():
    """Synthetic labels are rule-derived; this checks rule consistency, not market accuracy."""
    assert len(CASES) == 200
    intent_ok = 0
    for case in CASES:
        plan = plan_ticket(case)
        expected = case["expected"]
        assert plan.action == expected["action"], case["id"]
        assert plan.amount == expected["amount"], case["id"]
        assert plan.requires_human == expected["requires_human"], case["id"]
        intent_ok += int(plan.intent == expected["intent"])
    assert intent_ok / len(CASES) >= 0.85


def test_high_risk_and_partial_refund_are_review():
    assert plan_ticket(next(c for c in CASES if c["id"] == "T-036")).requires_human is True
    assert plan_ticket(next(c for c in CASES if c["id"] == "T-044")).action == "refund_reject"
    assert plan_ticket(next(c for c in CASES if c["id"] == "T-057")).action == "handoff"


def test_agent_uses_facts_not_ticket_id_for_amount():
    plan = plan_ticket(
        {
            "message": "1004 显示器坏了要退",
            "order_no": "1004",
            "facts": {
                "order_no": "1004",
                "title": "高端显示器",
                "status": "delivered",
                "paid": 100.0,
                "goods": 100.0,
                "shipping": 0.0,
                "delivered_days_ago": 2,
                "category": "digital",
                "returnable": True,
                "region": "上海",
            },
        }
    )
    assert plan.action == "refund_apply"
    assert plan.amount == 100.0
    assert plan.requires_human is False


def test_mismatched_facts_are_rejected():
    plan = plan_ticket(
        {
            "message": "1004 显示器坏了要退",
            "order_no": "1004",
            "facts": {"order_no": "1001", "status": "delivered", "paid": 1.0, "goods": 1.0},
        }
    )
    assert plan.action == "clarify"
    assert plan.amount is None
    assert "不一致" in plan.notes


def test_after_sales_plan_api():
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/after-sales/plan",
            json={
                "message": "1004 显示器坏了要退",
                "order_no": "1004",
                "facts": {"title": "高端显示器", "status": "delivered", "paid": 699.0, "goods": 699.0, "shipping": 0.0, "delivered_days_ago": 2, "category": "digital", "returnable": True, "region": "上海"},
            },
        )
        assert response.status_code == 200
        plan = response.json()["plan"]
        assert plan["action"] == "refund_apply"
        assert plan["amount"] == 699.0
        assert plan["requires_human"] is True
        assert plan["risk_level"] == "R3"
