from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import timedelta

from app.db import utcnow
from app.intent import extract_order_id
from app.models import Order, OrderItem
from app.refunds import evaluate_refund

REASON_PATTERNS = (
    ("quality", ("质量", "坏", "破损", "故障", "开胶", "杂音", "起球", "屏幕碎", "碎", "刻错", "异味")),
    ("wrong_item", ("发错", "错发", "错货")),
    ("missing", ("少件", "漏发", "少发", "少配件")),
    ("no_reason", ("不想要", "无理由", "七天", "7天", "尺码小", "换大一码")),
)


@dataclass
class TicketPlan:
    intent: str
    action: str
    amount: float | None = None
    reason_code: str | None = None
    requires_human: bool = False
    risk_level: str = "R0"
    policy_refs: list[str] = field(default_factory=list)
    notes: str = ""
    reply_draft: str = ""
    preview_only: bool = True
    facts_source: str = "none"

    def to_dict(self) -> dict:
        return asdict(self)


def _extract_reason(message: str) -> str | None:
    for reason, keywords in REASON_PATTERNS:
        if any(keyword in message for keyword in keywords):
            return reason
    return None


def _intent(message: str, order_no: str | None) -> str:
    q = message
    if any(k in q for k in ("报警", "被骗", "诈骗", "盗刷", "救命", "账号被盗")):
        return "emergency"
    if any(k in q for k in ("转人工", "真人", "人工客服", "叫人工", "我要人工", "主管")):
        return "human_request"
    if any(k in q for k in ("投诉", "态度", "太失望", "垃圾", "差评")):
        return "complaint"
    if any(k in q for k in ("别人的订单", "他人订单", "老板电话", "别人")):
        return "out_of_scope"
    if any(k in q for k in ("改地址", "改收货地址")):
        return "after_sales"
    if "售后好吗" in q or "售后怎么样" in q:
        return "general_qa"
    if "想退货" in q:
        return "after_sales"
    if "换" in q and not any(k in q for k in ("换货规则", "退换政策")):
        return "after_sales"
    if order_no and "取消" in q and "退款" in q:
        return "order_query"
    if order_no and any(k in q for k in ("到账", "退款到哪")):
        return "order_query"
    if order_no and any(k in q for k in ("发货了吗", "什么状态")) and not any(k in q for k in ("物流", "快递", "运单")):
        return "order_query"
    if order_no and "到" in q and not any(k in q for k in ("退款", "退货", "退钱", "能退", "退吗")):
        return "logistics_query"
    if not order_no and "能退吗" in q and any(k in q for k in ("这个", "那个", "它")):
        return "needs_clarify"
    if any(k in q for k in ("退款", "退货", "退钱", "能退", "退吗", "要退", "售后", "不想要", "无理由", "七天", "质量", "坏", "破损", "故障", "开胶", "杂音", "起球", "碎", "刻错", "异味", "发错", "错发", "少件", "漏发", "少发", "只退一半", "部分退款")):
        if any(k in q for k in ("规则", "政策", "怎么退", "退换规则", "流程", "超过", "期限", "几天", "多久", "到账", "定制品", "定制款能退", "定制商品", "特殊品类", "运费谁出", "扣运费", "承担", "审核", "条件", "满足", "从哪天", "开始算", "15 天", "能退吗")):
            return "policy_qa"
        return "refund_request"
    if any(k in q for k in ("物流", "快递", "到哪", "到了", "到货", "签收", "运单")):
        if not order_no and ("用什么快递" in q or "发什么快递" in q):
            return "general_qa"
        if not order_no and "三天没动" in q:
            return "needs_clarify"
        return "logistics_query"
    if any(k in q for k in ("发货了吗", "什么状态", "订单", "单号")) and order_no:
        return "order_query"
    if any(k in q for k in ("邮费", "运费", "包邮", "发票", "会员", "积分", "退换", "港澳台", "海外", "新疆", "西藏", "内蒙古", "偏远")):
        return "policy_qa"
    if "怎么选" in q and "鞋" in q:
        return "product_qa"
    if any(k in q for k in ("怎么选", "怎么用", "怎么保养", "有什么区别", "推荐", "哪个", "怎么买", "你们家", "货到付款", "快递", "便宜", "优惠", "折扣", "发货", "议价", "花呗", "赠品")):
        return "general_qa"
    if any(k in q for k in ("有货", "库存", "多少钱", "价格", "材质", "规格", "适合", "商品", "码", "尺码", "双卡", "支持", "机械轴", "能装", "保温", "续航", "内存", "颜色", "蓝色", "飞机", "保暖", "电脑")):
        return "product_qa"
    if "刚才" in q or "那个订单" in q:
        return "needs_clarify"
    if any(k in q for k in ("订单", "单号", "取消")):
        return "order_query"
    if any(k in q for k in ("你好", "您好", "谢谢", "在吗", "你是谁", "你能做什么", "机器人", "hi", "hello")):
        return "chitchat"
    if any(k in q for k in ("那个", "这个", "它", "刚才", "还是那个", "换一个")):
        return "needs_clarify"
    return "out_of_scope"


def _policy_refs(message: str, reason: str | None) -> list[str]:
    refs: list[str] = []
    if any(k in message for k in ("包邮", "满多少")):
        refs.append("shipping.md#1")
    if any(k in message for k in ("新疆", "西藏", "偏远", "内蒙古")):
        refs.append("shipping.md#3")
    if any(k in message for k in ("金卡", "银卡", "会员", "权益")):
        refs.extend(["shipping.md#5", "faq.md#3"])
    if "积分" in message:
        refs.append("shipping.md#6")
    if any(k in message for k in ("无理由", "7天", "七天")):
        refs.append("returns.md#1")
    if any(k in message for k in ("质量", "坏", "破损", "故障", "开胶", "杂音", "起球")):
        refs.append("returns.md#2")
    if any(k in message for k in ("定制", "定做")):
        refs.append("returns.md#3")
    if any(k in message for k in ("500", "审核", "高金额")):
        refs.append("returns.md#5")
    if any(k in message for k in ("运费谁出", "退货运费")):
        refs.append("shipping.md#7")
    if "港澳台" in message or "海外" in message:
        refs.append("shipping.md#4")
    if "发票" in message:
        refs.append("faq.md#4")
    if any(k in message for k in ("耳机", "有货", "保温杯", "商品")):
        refs.append("faq.md#1")
    if reason in {"quality", "damaged", "wrong_item", "missing"}:
        refs.append("returns.md#2")
    elif reason == "no_reason":
        refs.append("returns.md#1")
    return list(dict.fromkeys(refs))


def _order_from_facts(order_no: str, facts: dict) -> Order:
    days = facts.get("delivered_days_ago")
    delivered_at = utcnow() - timedelta(days=int(days)) if days is not None else None
    status = str(facts.get("status") or "delivered")
    paid = float(facts.get("paid") or 0)
    order = Order(
        id=order_no,
        order_no=order_no,
        user_id="merchant_ticket",
        title=str(facts.get("title") or "订单商品"),
        status=status,
        paid_amount=paid,
        goods_paid=float(facts.get("goods") or paid),
        shipping_fee=float(facts.get("shipping") or 0),
        refunded_amount=paid if status == "refunded" else 0.0,
        region=str(facts.get("region") or "上海"),
        delivered_at=delivered_at,
        created_at=utcnow() - timedelta(days=(int(days) + 10) if days is not None else 3),
    )
    order.items = [
        OrderItem(
            product_id=f"p_{order_no}",
            title=order.title,
            qty=1,
            unit_price=order.goods_paid,
            category=str(facts.get("category") or "general"),
            returnable=bool(facts.get("returnable", True)),
        )
    ]
    return order


def _risk(action: str, review: bool, amount: float | None) -> str:
    if action == "handoff":
        return "R4"
    if action in {"answer", "query_order", "query_logistics", "clarify"}:
        return "R0"
    if review and (amount or 0) > 500:
        return "R3"
    if review:
        return "R2"
    if action in {"refund_apply", "refund_reject", "exchange_apply", "cancel_order"}:
        return "R1"
    return "R0"


def _reply(action: str, amount: float | None, reason: str | None, review: bool) -> str:
    if action == "clarify":
        return "我需要确认一个关键信息：请提供订单号，或选择最近的订单。"
    if action == "handoff":
        return "这件事需要人工复核，我会把订单事实、政策和风险整理成工单，避免反复描述。"
    if action == "query_order":
        return "我会先查订单状态和金额，再把结果和下一步告诉你。"
    if action == "query_logistics":
        return "我会查物流轨迹；如果已签收但你未收到，我会转人工核实。"
    if action == "refund_apply":
        amount_text = f"退款金额 {amount:.2f} 元" if amount is not None else "退款金额待核算"
        return f"这笔订单符合退款条件，{amount_text}；{'需要主管审批后执行' if review else '可提交退款申请'}。"
    if action == "refund_reject":
        return "这笔订单暂不符合直接退款条件；我会给出具体政策依据，并提供申诉/人工复核路径。"
    if action == "exchange_apply":
        return "可以走换货流程；我会核对 SKU/库存和运费责任后提交。"
    if action == "cancel_order":
        return "订单未支付/未发货可以走取消；已支付/已发货需要拦截或人工审核。"
    if action == "answer":
        return "我会按平台政策/商品信息回答，并给出依据和下一步。"
    return "我先帮你确认订单和问题。"


def plan_ticket(payload: dict) -> TicketPlan:
    message = str(payload.get("message") or "")
    order_no = payload.get("order_no") or extract_order_id(message)
    facts = payload.get("facts") or {}
    facts_order = str(facts.get("order_no") or "")
    if order_no and facts_order and facts_order != str(order_no):
        return TicketPlan(
            intent="needs_clarify",
            action="clarify",
            requires_human=False,
            risk_level="R0",
            notes="订单号与订单事实不一致，拒绝用不一致事实做判责",
            reply_draft="订单信息不一致，我需要重新拉取订单事实后再判断。",
            preview_only=True,
            facts_source="request",
        )
    intent = _intent(message, str(order_no) if order_no else None)
    reason = _extract_reason(message)
    action = "answer"
    amount: float | None = None
    review = False
    notes = ""

    if intent in {"emergency", "human_request", "complaint"}:
        action = "handoff"
        review = True
        notes = "安全/情绪/明确人工，走商家审核或接管"
    elif intent == "needs_clarify":
        action = "clarify"
    elif intent in {"policy_qa", "product_qa", "general_qa", "chitchat", "out_of_scope"}:
        action = "answer"
        if intent == "out_of_scope":
            notes = "范围外，给边界和替代动作"
        elif intent == "policy_qa" and any(k in message for k in ("多久到账", "到账时效")):
            action = "handoff"
            review = True
            notes = "知识库未覆盖到账时效，转审核/补知识"
    elif intent == "order_query":
        if "取消" in message and "退款" in message:
            action = "answer"
            notes = "订单已取消，无款可退；引导查看退款状态"
        elif any(k in message for k in ("取消", "取消订单")):
            action = "cancel_order"
            if facts.get("status") not in {None, "pending_payment"}:
                review = True
                notes = "已支付/已发货取消需审核"
        else:
            action = "query_order"
    elif intent == "logistics_query":
        if not order_no:
            action = "clarify"
        elif any(k in message for k in ("没收到", "未收到", "显示签收")):
            action = "handoff"
            review = True
            notes = "签收争议，需商家/物流核实"
        else:
            action = "query_logistics"
    elif intent == "after_sales":
        if any(k in message for k in ("改地址", "改收货地址")):
            action = "handoff"
            review = True
            notes = "改地址受订单状态限制，转运营审核"
        elif "只退一半" in message or "部分退款" in message or "只退部分" in message:
            action = "handoff"
            review = True
            notes = "部分退规则未定义，转人工"
        elif "换" in message:
            action = "exchange_apply"
            notes = "换货需核对 SKU/库存/运费"
        else:
            action = "clarify"
    elif intent == "refund_request":
        status = str(facts.get("status") or "")
        if "只退一半" in message or "部分退款" in message or "只退部分" in message:
            action = "handoff"
            review = True
            notes = "部分退规则未定义，转人工"
        elif not order_no or not facts:
            action = "clarify"
        else:
            if not reason and status in {"paid", "shipped", "cancelled", "refunded", "refunding", "pending_payment"}:
                # 未发货/已发货/终态订单按整单退款规则处理，不强制追问原因。
                reason = "no_reason"
            if not reason:
                action = "clarify"
                notes = "缺退款原因，先问一个关键槽位"
            else:
                order = _order_from_facts(str(order_no), facts)
                decision = evaluate_refund(order, reason)
                amount = round(decision.amount, 2) if decision.eligible else 0.0
                action = "refund_apply" if decision.eligible else "refund_reject"
                review = decision.requires_human
                notes = "；".join(decision.reasons)

    refs = _policy_refs(message, reason)
    risk = _risk(action, review, amount)
    return TicketPlan(
        intent=intent,
        action=action,
        amount=amount,
        reason_code=reason,
        requires_human=review or action == "handoff",
        risk_level=risk,
        policy_refs=refs,
        notes=notes,
        reply_draft=_reply(action, amount, reason, review),
        preview_only=True,
        facts_source="request" if facts else "none",
    )
