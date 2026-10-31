from __future__ import annotations

from datetime import timedelta

from app.db import utcnow
from app.intent import classify, extract_order_id, rewrite_query
from app.knowledge import get_kb
from app.models import Order, OrderItem
from app.refunds import evaluate_refund
from app.services import pii_scrub


def make_order(status: str = "delivered", paid: float = 299.0, goods: float = 299.0, shipping: float = 0.0):
    order = Order(
        id="x1",
        order_no="x1",
        user_id="u_demo",
        title="测试商品",
        status=status,
        paid_amount=paid,
        goods_paid=goods,
        shipping_fee=shipping,
        delivered_at=utcnow() - timedelta(days=3),
    )
    order.items = [OrderItem(product_id="p1", title="测试商品", qty=1, unit_price=goods, category="general", returnable=True)]
    return order


def test_intent_and_order_id():
    assert classify("邮费是多少") == "policy_qa"
    assert classify("我要退款，质量问题") == "refund_request"
    assert classify("转人工") == "human_request"
    assert extract_order_id("订单 1001 到哪了") == "1001"
    assert "1001" in rewrite_query("那它能退吗", [{"role": "user", "content": "订单 1001 到货了吗"}])


def test_retrieval_shipping_rules():
    docs = get_kb().retrieve("邮费是多少", category="shipping", top_k=8)
    assert len(docs) >= 6
    joined = "\n".join(d.content for d in docs)
    assert "99" in joined and "偏远" in joined and "会员" in joined


def test_refund_boundaries():
    paid = make_order("paid", paid=89, goods=83, shipping=6)
    decision = evaluate_refund(paid, "no_reason")
    assert decision.eligible and decision.amount == 89 and decision.shipping_payer == "platform"

    shipped = make_order("shipped", paid=159, goods=153, shipping=6)
    assert evaluate_refund(shipped, "quality").requires_human is True

    delivered = make_order("delivered", paid=299, goods=299)
    quality = evaluate_refund(delivered, "quality")
    assert quality.eligible and quality.amount == 299

    no_reason = evaluate_refund(delivered, "no_reason")
    assert no_reason.eligible and no_reason.amount == 299 and no_reason.shipping_payer == "user"

    high = make_order("delivered", paid=699, goods=699)
    assert evaluate_refund(high, "quality").requires_human is True

    custom = make_order("delivered", paid=399, goods=399)
    custom.items[0].returnable = False
    assert evaluate_refund(custom, "quality").requires_human is True

    unknown = evaluate_refund(delivered, "other")
    assert unknown.requires_human is True


def test_pii_scrub():
    text = "我的手机 13812345678，地址是上海市浦东新区"
    out = pii_scrub(text)
    assert "13812345678" not in out
    assert "138****0000" in out
