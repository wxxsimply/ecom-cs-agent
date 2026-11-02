"""Validate the synthetic ticket set against the rules and the knowledge files.

Run: python scripts/validate_eval_tickets.py
Exit code 1 means at least one label is inconsistent.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.build_eval_tickets import ORDERS, refund_decision

DATASET = ROOT / "data" / "eval" / "tickets_v1.jsonl"
KNOWLEDGE = ROOT / "data" / "knowledge"

INTENTS = {
    "policy_qa", "product_qa", "general_qa", "order_query", "logistics_query",
    "refund_request", "after_sales", "complaint", "chitchat", "human_handoff",
    "human_request", "emergency", "needs_clarify", "out_of_scope",
}
ACTIONS = {
    "answer", "query_order", "query_logistics", "refund_apply", "refund_reject",
    "return_apply", "exchange_apply", "cancel_order", "handoff", "clarify",
}
REFUND_REASONS = {"quality", "wrong_item", "damaged", "missing", "no_reason"}
PII_PATTERNS = (
    re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"),          # mainland mobile
    re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"),           # email
    re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)"),         # 18-digit id
    re.compile(r"(?<!\d)\d{16,19}(?!\d)"),             # bank card
    re.compile(r"(?<!\d)\d{6,}(?:号|室|栋|单元)"),      # address-ish
)


def _sections(name: str) -> int:
    text = (KNOWLEDGE / name).read_text(encoding="utf-8")
    return len(re.findall(r"^##\s+", text, flags=re.M))


def validate(path: Path = DATASET) -> tuple[list[str], list[dict]]:
    errors: list[str] = []
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    seen: set[str] = set()

    for row in rows:
        rid = row.get("id", "<no-id>")

        def bad(message: str) -> None:
            errors.append(f"{rid}: {message}")

        if rid in seen:
            bad("duplicate id")
        seen.add(rid)

        if row.get("split") not in {"dev", "holdout"}:
            bad("split must be dev/holdout")
        if row.get("source") != "synthetic":
            bad("source must be synthetic")
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(row.get("scenario_date", ""))):
            bad("scenario_date must be YYYY-MM-DD")
        if row.get("channel") not in {"web", "wechat", "wecom"}:
            bad("unsupported channel")
        if not isinstance(row.get("message"), str) or not row["message"].strip():
            bad("message is empty")
        if not isinstance(row.get("history"), list):
            bad("history must be a list")

        order_no = row.get("order_no")
        facts = row.get("facts")
        if order_no is not None and order_no not in ORDERS:
            bad(f"unknown order {order_no}")
        if order_no is None and facts is not None:
            bad("facts must be null when order_no is null")
        if order_no is not None:
            expected_facts = ORDERS[order_no]
            for key, source_key in (("status", "status"), ("paid", "paid"), ("goods", "goods"), ("shipping", "shipping"), ("delivered_days_ago", "days"), ("category", "category"), ("returnable", "returnable"), ("region", "region")):
                if (facts or {}).get(key) != expected_facts[source_key]:
                    bad(f"facts.{key} does not match seed order {order_no}")

        expected = row.get("expected") or {}
        intent = expected.get("intent")
        action = expected.get("action")
        if intent not in INTENTS:
            bad(f"unknown intent {intent}")
        if action not in ACTIONS:
            bad(f"unknown action {action}")

        refs = expected.get("policy_refs") or []
        if not isinstance(refs, list):
            bad("policy_refs must be a list")
        for ref in refs:
            match = re.fullmatch(r"(faq|returns|shipping)\.md#(\d+)", ref)
            if not match:
                bad(f"bad policy ref {ref}")
            elif int(match.group(2)) > _sections(f"{match.group(1)}.md"):
                bad(f"policy ref out of range: {ref}")

        handoff = expected.get("handoff_reason")
        if action == "handoff":
            if not handoff:
                bad("handoff action needs handoff_reason")
            if expected.get("requires_human") is not True:
                bad("handoff action must require human")
        elif handoff and action not in {"refund_apply", "refund_reject"}:
            bad("handoff_reason set on non-handoff action")
        elif handoff and expected.get("requires_human") is not True:
            bad("handoff_reason on refund action must require human")

        if action in {"refund_apply", "refund_reject"}:
            reason = expected.get("reason_code")
            status = ORDERS[order_no]["status"] if order_no in ORDERS else None
            terminal = status in {"cancelled", "refunded", "refunding", "pending_payment"}
            if reason not in REFUND_REASONS and not (reason is None and terminal):
                bad(f"refund action needs a reason, got {reason}")
            eligible, amount, human, why = refund_decision(order_no, reason or "no_reason")
            if expected.get("amount") != (amount if eligible else 0.0):
                bad(f"amount mismatch: got {expected.get('amount')}, expected {amount if eligible else 0.0} ({why})")
            if expected.get("requires_human") is not (human or bool(handoff)):
                bad(f"requires_human mismatch for {order_no}/{reason}")
            if action == "refund_apply" and not eligible:
                bad(f"refund_apply but rule says not eligible ({why})")
            if action == "refund_reject" and eligible:
                bad(f"refund_reject but rule says eligible ({why})")
        elif expected.get("amount") is not None:
            bad("amount must be null for non-refund actions")

        for text in (row.get("message") or "", expected.get("notes") or ""):
            for pattern in PII_PATTERNS:
                if pattern.search(text):
                    bad(f"possible PII: {pattern.pattern}")

    return errors, rows


def main() -> int:
    errors, rows = validate()
    summary = {
        "rows": len(rows),
        "dev": sum(1 for row in rows if row["split"] == "dev"),
        "holdout": sum(1 for row in rows if row["split"] == "holdout"),
        "intents": len({row["expected"]["intent"] for row in rows}),
        "actions": len({row["expected"]["action"] for row in rows}),
        "handoff": sum(1 for row in rows if row["expected"]["action"] == "handoff"),
        "refund": sum(1 for row in rows if row["expected"]["action"] in {"refund_apply", "refund_reject"}),
    }
    print(json.dumps(summary, ensure_ascii=False))
    for error in errors:
        print("FAIL", error)
    print("OK" if not errors else f"{len(errors)} error(s)")
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
