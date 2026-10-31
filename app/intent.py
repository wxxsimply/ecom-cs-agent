from __future__ import annotations

import re

ORDER_RE = re.compile(r"(?<!\d)(\d{4,12})(?!\d)")

INTENTS = (
    "policy_qa",
    "product_qa",
    "general_qa",
    "order_query",
    "logistics_query",
    "refund_request",
    "after_sales",
    "complaint",
    "chitchat",
    "human_handoff",
    "human_request",
    "emergency",
    "needs_clarify",
    "out_of_scope",
)


def extract_order_id(text: str) -> str | None:
    match = ORDER_RE.search(text or "")
    return match.group(1) if match else None


def last_order_id(history: list[dict]) -> str | None:
    for msg in reversed(history):
        found = extract_order_id(str(msg.get("content", "")))
        if found:
            return found
    return None


def classify(text: str, history: list[dict] | None = None) -> str:
    q = (text or "").strip()
    if any(k in q for k in ("报警", "被骗", "诈骗", "盗刷", "救命", "人身安全", "账号被盗")):
        return "emergency"
    if any(k in q for k in ("别人", "他人", "其他人的订单")) and any(k in q for k in ("订单", "退", "卡", "查")):
        return "out_of_scope"
    if any(k in q for k in ("转人工", "真人", "人工客服", "叫人工")):
        return "human_request"
    if any(k in q for k in ("退款", "退货", "退钱", "能退", "退吗", "售后", "换货", "坏了", "破损", "少件", "漏发")):
        if any(k in q for k in ("规则", "政策", "怎么退", "退换规则", "流程", "超过", "期限", "几天", "7天", "七天", "多久", "到账", "定制品", "特殊品类", "运费谁出")):
            return "policy_qa"
        return "refund_request"
    if any(k in q for k in ("投诉", "态度", "太失望", "垃圾", "差评")):
        return "complaint"
    if any(k in q for k in ("物流", "快递", "到哪", "到了", "到货", "发货", "签收", "运单")):
        return "logistics_query"
    if any(k in q for k in ("订单", "单号")) and extract_order_id(q):
        return "order_query"
    if any(k in q for k in ("有货", "库存", "多少钱", "价格", "材质", "规格", "适合", "商品")):
        return "product_qa"
    if any(k in q for k in ("邮费", "运费", "包邮", "发票", "会员", "积分", "退换", "政策")):
        return "policy_qa"
    if any(k in q for k in ("怎么选", "怎么用", "怎么保养", "有什么区别", "推荐", "哪个好", "怎么买")):
        return "general_qa"
    if any(k in q for k in ("订单", "单号")):
        return "order_query"
    if any(k in q for k in ("你好", "您好", "谢谢", "在吗", "你是谁", "你能做什么", "可以做什么", "会什么", "机器人", "是 ai", "是ai", "hi", "hello")):
        return "chitchat"
    if extract_order_id(q):
        return "order_query"
    if any(k in q for k in ("那个", "这个", "它", "刚才", "还是那个", "换一个")):
        return "needs_clarify"
    return "out_of_scope"


def extract_reason(text: str) -> str | None:
    q = text or ""
    if any(k in q for k in ("质量", "坏", "破损", "故障")):
        return "quality"
    if any(k in q for k in ("发错", "错发", "错货")):
        return "wrong_item"
    if any(k in q for k in ("少件", "漏发", "少发")):
        return "missing"
    if any(k in q for k in ("不想要", "无理由", "七天", "7天")):
        return "no_reason"
    return None


def rewrite_query(text: str, history: list[dict] | None = None) -> str:
    q = (text or "").strip()
    if any(k in q for k in ("它", "那单", "这单", "那个", "这个")) and not extract_order_id(q):
        oid = last_order_id(history or [])
        if oid:
            return f"订单 {oid}：{q}"
    return q
