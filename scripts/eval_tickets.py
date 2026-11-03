"""P1 minimal ticket evaluation.

Run: python scripts/eval_tickets.py
This is a deterministic baseline, not an LLM evaluation. Labels are synthetic
and derived from the same rules, so 100% action/amount here means
"rule consistency", not real-world accuracy.
"""

from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.after_sales_agent import plan_ticket  # noqa: E402

DATASET = ROOT / "data" / "eval" / "tickets_v1.jsonl"


def evaluate() -> dict:
    rows = [json.loads(line) for line in DATASET.read_text(encoding="utf-8").splitlines() if line.strip()]
    per_split: dict[str, dict[str, int]] = defaultdict(lambda: Counter())
    per_action: dict[str, Counter] = defaultdict(Counter)
    hard_failures = []
    intent_mismatches = []
    for row in rows:
        expected = row["expected"]
        plan = plan_ticket(row)
        split = row["split"]
        action = expected["action"]
        checks = {
            "intent": plan.intent == expected["intent"],
            "action": plan.action == action,
            "amount": plan.amount == expected["amount"],
            "review": plan.requires_human == expected["requires_human"],
        }
        for key, ok in checks.items():
            per_split[split][f"{key}_ok"] += int(ok)
            per_split[split][f"{key}_n"] += 1
            per_action[action][f"{key}_ok"] += int(ok)
            per_action[action][f"{key}_n"] += 1
        record = {
            "id": row["id"],
            "split": split,
            "message": row["message"],
            "expected": {
                "intent": expected["intent"],
                "action": action,
                "amount": expected["amount"],
                "review": expected["requires_human"],
            },
            "got": plan.to_dict(),
            "checks": checks,
        }
        if not all(checks[key] for key in ("action", "amount", "review")):
            hard_failures.append(record)
        elif not checks["intent"]:
            intent_mismatches.append(record)
    return {
        "rows": rows,
        "per_split": per_split,
        "per_action": per_action,
        "hard_failures": hard_failures,
        "intent_mismatches": intent_mismatches,
    }


def pct(counter: Counter, key: str) -> float:
    n = counter.get(f"{key}_n", 0)
    return round(100.0 * counter.get(f"{key}_ok", 0) / n, 2) if n else 0.0


def main() -> int:
    result = evaluate()
    rows = result["rows"]
    summary = {}
    for split in ("dev", "holdout"):
        c = result["per_split"][split]
        summary[split] = {
            "n": c["action_n"],
            "intent_accuracy": pct(c, "intent"),
            "action_accuracy": pct(c, "action"),
            "amount_accuracy": pct(c, "amount"),
            "review_accuracy": pct(c, "review"),
        }
    print(
        json.dumps(
            {
                "summary": summary,
                "hard_failures": len(result["hard_failures"]),
                "intent_mismatches": len(result["intent_mismatches"]),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    for failure in result["hard_failures"][:20]:
        print("FAIL", json.dumps(failure, ensure_ascii=False))
    for mismatch in result["intent_mismatches"][:5]:
        print("INTENT_MISMATCH", json.dumps(mismatch, ensure_ascii=False))

    holdout = result["per_split"]["holdout"]
    dev = result["per_split"]["dev"]
    all_amount = (
        (dev["amount_ok"] + holdout["amount_ok"])
        / (dev["amount_n"] + holdout["amount_n"])
    ) * 100.0
    hard_gates = [
        ("holdout action", pct(holdout, "action") >= 85.0),
        ("holdout amount", pct(holdout, "amount") == 100.0),
        ("holdout review", pct(holdout, "review") >= 100.0),
        ("all amount", all_amount == 100.0),
        ("all review", ((dev["review_ok"] + holdout["review_ok"]) / (dev["review_n"] + holdout["review_n"])) * 100.0 == 100.0),
    ]
    print("GATES", dict(hard_gates))
    return 0 if all(ok for _, ok in hard_gates) else 1


if __name__ == "__main__":
    raise SystemExit(main())
