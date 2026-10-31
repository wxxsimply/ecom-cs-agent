from __future__ import annotations

import json
import re
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app

ROOT = Path(__file__).resolve().parent.parent
CASES = [json.loads(line) for line in (ROOT / "data" / "eval" / "answer_first_v1.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]


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


def run_case(client: TestClient, case: dict) -> tuple[str, dict | None]:
    with client.stream(
        "POST",
        "/api/v1/chat/stream",
        headers={"X-User-Id": "u_demo"},
        json={"session_id": None, "message": case["message"], "client_message_id": case["id"], "context": {}},
    ) as response:
        assert response.status_code == 200
        events = parse_sse("".join(response.iter_text()))
    interrupt = next((e["data"] for e in events if e["event"] == "interrupt"), None)
    answer = "".join(e["data"].get("delta", "") for e in events if e["event"] == "token")
    return answer, interrupt


def test_fifty_answer_first_cases():
    assert len(CASES) == 50
    failures = []
    with TestClient(app) as client:
        for case in CASES:
            answer, interrupt = run_case(client, case)
            if interrupt is not None:
                # 订单选择/确认卡是合法交互，不是机械拒答。
                assert interrupt.get("options") is not None or interrupt.get("confirm_token")
                continue
            for banned in case["forbidden_any"]:
                if banned in answer:
                    failures.append(f"{case['id']} contains banned: {banned}")
            if not any(keyword in answer for keyword in case["expected_any"]):
                failures.append(f"{case['id']} missing expected: {answer[:120]}")
            if len(answer.strip()) < 10:
                failures.append(f"{case['id']} too short: {answer!r}")
    assert not failures, "\n".join(failures)


def test_eval_file_has_fifty_cases_and_required_fields():
    assert len(CASES) == 50
    assert {case["id"] for case in CASES} == {f"AF-{i:02d}" for i in range(1, 51)}
    for case in CASES:
        assert case["message"]
        assert case["expected_any"]
        assert case["forbidden_any"]
