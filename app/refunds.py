from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from app.db import utcnow
from app.models import Order


HUMAN_REVIEW_LIMIT = 500.0
QUALITY_DAYS = 15
NO_REASON_DAYS = 7


@dataclass
class RefundDecision:
    eligible: bool
    reason_code: str
    reasons: list[str]
    amount: float
    amount_breakdown: dict = field(default_factory=dict)
    shipping_payer: str = "none"
    requires_human: bool = False
    policy_snapshot: dict = field(default_factory=dict)


def _days_since(value: datetime | None) -> int | None:
    if value is None:
        return None
    delta = utcnow() - value
    return max(0, delta.days)


def evaluate_refund(order: Order, reason_code: str) -> RefundDecision:
    paid_amount = float(order.paid_amount or 0)
    goods_paid = float(order.goods_paid or 0)
    shipping_fee = float(order.shipping_fee or 0)
    refunded = float(order.refunded_amount or 0)
    snapshot = {
        "policy_version": "2026-10",
        "quality_days": QUALITY_DAYS,
        "no_reason_days": NO_REASON_DAYS,
        "human_review_limit": HUMAN_REVIEW_LIMIT,
    }
    if order.status in {"cancelled", "refunded", "refunding"}:
        return RefundDecision(False, reason_code, ["订单已取消/退款中/已退款"], 0.0, policy_snapshot=snapshot)
    if order.status == "pending_payment":
        return RefundDecision(False, reason_code, ["订单未支付，请到订单页取消"], 0.0, policy_snapshot=snapshot)
    if order.status == "paid":
        amount = min(paid_amount, paid_amount - refunded)
        return RefundDecision(
            True, reason_code, ["订单未发货，支持全额退款"], round(amount, 2),
            {"goods": round(goods_paid, 2), "shipping": round(shipping_fee, 2), "points_refund": order.points_used or 0},
            "platform", amount > HUMAN_REVIEW_LIMIT, snapshot,
        )
    if order.status == "shipped":
        amount = min(paid_amount, paid_amount - refunded)
        return RefundDecision(
            True, reason_code, ["订单已发货，需要拦截/拒收，转人工处理"], round(amount, 2),
            {"goods": round(goods_paid, 2), "shipping": round(shipping_fee, 2), "points_refund": order.points_used or 0},
            "platform", True, snapshot,
        )
    if order.delivered_at is None:
        return RefundDecision(False, reason_code, ["缺少签收时间，转人工核实"], 0.0, policy_snapshot=snapshot, requires_human=True)

    days = _days_since(order.delivered_at) or 0
    quality_reasons = {"quality", "wrong_item", "damaged", "missing"}
    if reason_code in quality_reasons:
        if days > QUALITY_DAYS:
            return RefundDecision(False, reason_code, [f"超过质量问题售后期限（{QUALITY_DAYS} 天）"], 0.0, policy_snapshot=snapshot, requires_human=True)
        amount = min(paid_amount - refunded, goods_paid + shipping_fee)
        return RefundDecision(
            True, reason_code, ["质量问题/错发/破损，平台承担运费"], round(amount, 2),
            {"goods": round(goods_paid, 2), "shipping": round(shipping_fee, 2), "points_refund": order.points_used or 0},
            "platform", amount > HUMAN_REVIEW_LIMIT, snapshot,
        )

    if reason_code != "no_reason":
        return RefundDecision(False, reason_code, ["退款原因需要人工确认"], 0.0, policy_snapshot=snapshot, requires_human=True)
    if days > NO_REASON_DAYS:
        return RefundDecision(False, reason_code, [f"超过无理由退货期限（{NO_REASON_DAYS} 天）"], 0.0, policy_snapshot=snapshot)
    if any(not item.returnable for item in order.items):
        return RefundDecision(False, reason_code, ["含不支持无理由退货的商品"], 0.0, policy_snapshot=snapshot, requires_human=True)
    amount = min(goods_paid, paid_amount - refunded)
    return RefundDecision(
        True, reason_code, [f"支持 {NO_REASON_DAYS} 天无理由退货"], round(amount, 2),
        {"goods": round(goods_paid, 2), "shipping": 0.0, "points_refund": order.points_used or 0},
        "user", amount > HUMAN_REVIEW_LIMIT, snapshot,
    )
