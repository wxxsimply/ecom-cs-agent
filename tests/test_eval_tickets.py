from __future__ import annotations

from scripts.validate_eval_tickets import validate


def test_ticket_dataset_is_valid():
    errors, rows = validate()
    assert errors == []
    assert len(rows) == 200
    assert sum(row["split"] == "dev" for row in rows) == 150
    assert sum(row["split"] == "holdout" for row in rows) == 50
    assert len({row["expected"]["intent"] for row in rows}) >= 13


def test_refund_labels_match_the_service_rules():
    _, rows = validate()
    by_id = {row["id"]: row for row in rows}

    # 699 > 500 -> human review even though quality issue is within 15 days
    assert by_id["T-036"]["expected"]["amount"] == 699.0
    assert by_id["T-036"]["expected"]["requires_human"] is True

    # no-reason return only refunds goods (233), shipping is on the user
    assert by_id["T-042"]["expected"]["amount"] == 233.0
    assert by_id["T-042"]["expected"]["requires_human"] is False

    # delivered 20 days ago -> outside the 7-day no-reason window
    assert by_id["T-038"]["expected"]["action"] == "refund_reject"
    assert by_id["T-038"]["expected"]["amount"] == 0.0

    # custom item -> human review even below 500
    assert by_id["T-043"]["expected"]["requires_human"] is True
