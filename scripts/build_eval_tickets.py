"""Build the synthetic, de-identified ticket set used for the portfolio evaluation.

The labels are derived from the same rules the service implements:
- refunds.py: QUALITY_DAYS=15, NO_REASON_DAYS=7, HUMAN_REVIEW_LIMIT=500
- data/knowledge/*.md: shipping / returns / faq sections
- scripts/seed.py: the 12 demo orders (amounts, status, delivered_days_ago)

Run: python scripts/build_eval_tickets.py
Output: data/eval/tickets_v1.jsonl and data/eval/tickets_v1.md
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_JSONL = ROOT / "data" / "eval" / "tickets_v1.jsonl"
OUT_MD = ROOT / "data" / "eval" / "tickets_v1.md"
SCENARIO_DATE = "2026-11-03"

QUALITY_DAYS = 15
NO_REASON_DAYS = 7
HUMAN_REVIEW_LIMIT = 500.0

ORDERS = {
    "1001": dict(title="无线耳机", status="delivered", paid=299.0, goods=299.0, shipping=0.0, days=3, category="digital", returnable=True, region="上海"),
    "1002": dict(title="保温杯", status="paid", paid=89.0, goods=83.0, shipping=6.0, days=None, category="home", returnable=True, region="上海"),
    "1003": dict(title="机械键盘", status="shipped", paid=159.0, goods=153.0, shipping=6.0, days=None, category="digital", returnable=True, region="新疆"),
    "1004": dict(title="高端显示器", status="delivered", paid=699.0, goods=699.0, shipping=0.0, days=2, category="digital", returnable=True, region="上海"),
    "1005": dict(title="夏季连衣裙", status="delivered", paid=129.0, goods=129.0, shipping=0.0, days=20, category="clothing", returnable=True, region="杭州"),
    "1006": dict(title="运动鞋", status="delivered", paid=239.0, goods=233.0, shipping=6.0, days=6, category="shoes", returnable=True, region="上海"),
    "1007": dict(title="定制刻字项链", status="delivered", paid=399.0, goods=399.0, shipping=0.0, days=4, category="custom", returnable=False, region="上海"),
    "1008": dict(title="便携充电宝", status="cancelled", paid=99.0, goods=99.0, shipping=0.0, days=None, category="digital", returnable=True, region="上海"),
    "1009": dict(title="蓝牙音箱", status="refunded", paid=199.0, goods=199.0, shipping=0.0, days=12, category="digital", returnable=True, region="上海"),
    "1010": dict(title="旗舰手机", status="paid", paid=699.0, goods=693.0, shipping=6.0, days=None, category="digital", returnable=True, region="上海"),
    "1011": dict(title="冬季羽绒服", status="shipped", paid=459.0, goods=453.0, shipping=6.0, days=None, category="clothing", returnable=True, region="哈尔滨"),
    "1012": dict(title="预售款背包", status="pending_payment", paid=159.0, goods=153.0, shipping=6.0, days=None, category="bag", returnable=True, region="上海"),
}


def refund_decision(order_no: str, reason: str) -> tuple[bool, float, bool, str]:
    """Mirror app/refunds.py so the gold label cannot drift from the service."""
    order = ORDERS[order_no]
    if order["status"] in {"cancelled", "refunded", "refunding"}:
        return False, 0.0, False, "订单已取消/退款中/已退款"
    if order["status"] == "pending_payment":
        return False, 0.0, False, "订单未支付"
    if order["status"] == "paid":
        amount = order["paid"]
        return True, round(amount, 2), amount > HUMAN_REVIEW_LIMIT, "未发货全额退款"
    if order["status"] == "shipped":
        return True, round(order["paid"], 2), True, "已发货需拦截/拒收，转人工"
    days = order["days"]
    if reason in {"quality", "wrong_item", "damaged", "missing"}:
        if days is None:
            return False, 0.0, True, "缺少签收时间"
        if days > QUALITY_DAYS:
            return False, 0.0, True, f"超过质量问题售后期（{QUALITY_DAYS} 天）"
        amount = min(order["paid"], order["goods"] + order["shipping"])
        return True, round(amount, 2), amount > HUMAN_REVIEW_LIMIT or not order["returnable"], "质量问题/错发/破损/漏发，平台承担运费"
    if reason != "no_reason":
        return False, 0.0, True, "退款原因需人工确认"
    if days is None:
        return False, 0.0, True, "缺少签收时间"
    if days > NO_REASON_DAYS:
        return False, 0.0, False, f"超过无理由退货期（{NO_REASON_DAYS} 天）"
    if not order["returnable"]:
        return False, 0.0, True, "含不支持无理由退货的商品"
    amount = min(order["goods"], order["paid"])
    return True, round(amount, 2), amount > HUMAN_REVIEW_LIMIT, "无理由退货，运费由用户承担"


# (id, message, order_no, intent, action, reason, refs, handoff, notes, history)
TICKETS = [
    # 售前政策
    ("T-001", "满多少包邮？", None, "policy_qa", "answer", None, ["shipping.md#1"], None, "包邮门槛 99 元", ()),
    ("T-002", "新疆要加运费吗？", "1003", "policy_qa", "answer", None, ["shipping.md#3"], None, "偏远地区加收 12 元，附加费不参与包邮", ()),
    ("T-003", "金卡会员免几次运费？", None, "policy_qa", "answer", None, ["shipping.md#5", "faq.md#3"], None, "金卡 3 次/月，银卡 1 次/月", ()),
    ("T-004", "积分怎么抵运费？", None, "policy_qa", "answer", None, ["shipping.md#6"], None, "100 积分抵 1 元，单笔最多 30 元", ()),
    ("T-005", "无理由退货几天？", None, "policy_qa", "answer", None, ["returns.md#1"], None, "签收后 7 天内，商品完好", ()),
    ("T-006", "质量问题多久能退？", None, "policy_qa", "answer", None, ["returns.md#2"], None, "签收后 15 天内，平台承担运费", ()),
    ("T-007", "定制款能退吗？", "1007", "policy_qa", "answer", None, ["returns.md#3"], None, "定制品不支持无理由退货", ()),
    ("T-008", "退款超过 500 要审核吗？", None, "policy_qa", "answer", None, ["returns.md#5"], None, "超过 500 元、特殊品类、已发货仅退款需人工审核", ()),
    ("T-009", "退货运费谁出？", None, "policy_qa", "answer", None, ["shipping.md#7"], None, "质量问题平台承担，无理由用户承担", ()),
    ("T-010", "港澳台能发货吗？", None, "policy_qa", "answer", None, ["shipping.md#4"], None, "港澳台及海外暂不支持自营配送", ()),
    ("T-011", "发票怎么开？", None, "policy_qa", "answer", None, ["faq.md#4"], None, "订单完成后可申请电子发票，企业抬头转人工", ()),
    ("T-012", "会员有什么权益？", None, "policy_qa", "answer", None, ["faq.md#3", "shipping.md#5"], None, "金卡/银卡免运费次数", ()),
    # 商品/通用
    ("T-013", "耳机有货吗？", None, "product_qa", "answer", None, ["faq.md#1"], None, "回答了商品信息；库存以商品页为准", ()),
    ("T-014", "连衣裙有 M 码吗？", None, "product_qa", "answer", None, [], None, "商品库无实时库存，引导查看商品页", ()),
    ("T-015", "手机支持双卡吗？", None, "product_qa", "answer", None, [], None, "商品参数问题，知识库未覆盖则说明边界", ()),
    ("T-016", "键盘是机械轴吗？", None, "product_qa", "answer", None, [], None, "同上", ()),
    ("T-017", "保温杯能装热水吗？", None, "product_qa", "answer", None, ["faq.md#2"], None, "304 不锈钢，支持七天无理由", ()),
    ("T-018", "哪个耳机适合跑步？", None, "general_qa", "answer", None, ["faq.md#1"], None, "按商品信息给建议，不编造参数", ()),
    ("T-019", "你们家发货快吗？", None, "general_qa", "answer", None, ["shipping.md#3"], None, "说明发货/时效边界，不承诺未确认的时效", ()),
    ("T-020", "能便宜点吗？", None, "general_qa", "answer", None, [], None, "自营不议价，说明优惠以页面为准", ()),
    ("T-021", "可以货到付款吗？", None, "general_qa", "answer", None, [], None, "支付方式问题，知识库未覆盖则说明边界", ()),
    ("T-022", "你们用什么快递？", None, "general_qa", "answer", None, [], None, "承运商以订单为准", ()),
    # 订单/物流
    ("T-023", "查下订单 1001", "1001", "order_query", "query_order", None, [], None, "查询订单状态与金额", ()),
    ("T-024", "1002 发货了吗？", "1002", "order_query", "query_order", None, [], None, "未发货，说明状态", ()),
    ("T-025", "1003 物流到哪了？", "1003", "logistics_query", "query_logistics", None, [], None, "运输中，返回轨迹", ()),
    ("T-026", "1004 签收了吗？", "1004", "logistics_query", "query_logistics", None, [], None, "已签收，返回完整轨迹", ()),
    ("T-027", "1011 到哈尔滨了吗？", "1011", "logistics_query", "query_logistics", None, ["shipping.md#3"], None, "偏远地区时效顺延 2–4 天", ()),
    ("T-028", "订单 1005 什么状态？", "1005", "order_query", "query_order", None, [], None, "已签收 20 天", ()),
    ("T-029", "我的快递三天没动了", None, "needs_clarify", "clarify", None, [], None, "缺订单号，先澄清或列最近订单", ()),
    ("T-030", "1009 退款到账了吗？", "1009", "order_query", "query_order", None, [], None, "已退款订单，说明退款状态", ()),
    ("T-031", "1012 还没付款怎么取消？", "1012", "order_query", "cancel_order", None, [], None, "未支付订单引导取消", ()),
    ("T-032", "1008 取消了怎么退款？", "1008", "order_query", "answer", None, [], None, "已取消订单，无款可退", ()),
    ("T-033", "1006 显示签收但我没收到", "1006", "logistics_query", "handoff", None, [], "delivery_dispute", "签收争议，转人工核实", ()),
    # 退款/售后
    ("T-034", "1001 耳机有杂音，想退款", "1001", "refund_request", "refund_apply", "quality", ["returns.md#2"], None, "15 天内质量问题", ()),
    ("T-035", "1001 不想要了，七天无理由", "1001", "refund_request", "refund_apply", "no_reason", ["returns.md#1"], None, "无理由，用户承担运费", ()),
    ("T-036", "1004 显示器坏了要退", "1004", "refund_request", "refund_apply", "quality", ["returns.md#2", "returns.md#5"], None, "金额 699 超 500，需人工", ()),
    ("T-037", "1004 不想要了", "1004", "refund_request", "refund_apply", "no_reason", ["returns.md#1", "returns.md#5"], None, "无理由金额超 500，需人工", ()),
    ("T-038", "1005 裙子不想要了", "1005", "refund_request", "refund_reject", "no_reason", ["returns.md#1"], None, "签收 20 天，超 7 天", ()),
    ("T-039", "1005 裙子起球，质量问题", "1005", "refund_request", "refund_reject", "quality", ["returns.md#2"], "quality_timeout", "签收 20 天，超 15 天，转人工", ()),
    ("T-040", "1006 鞋码小了，想换大一码", "1006", "after_sales", "exchange_apply", None, ["returns.md#1"], None, "换货；无理由运费用户承担", ()),
    ("T-041", "1006 鞋子开胶了", "1006", "refund_request", "refund_apply", "quality", ["returns.md#2"], None, "6 天内质量问题", ()),
    ("T-042", "1006 不想要了", "1006", "refund_request", "refund_apply", "no_reason", ["returns.md#1"], None, "无理由只退商品金额 233", ()),
    ("T-043", "1007 项链刻错字了", "1007", "refund_request", "refund_apply", "quality", ["returns.md#2", "returns.md#3"], None, "定制品，质量问题需人工", ()),
    ("T-044", "1007 定制款不想要了", "1007", "refund_request", "refund_reject", "no_reason", ["returns.md#3"], "custom_item", "定制品不支持无理由", ()),
    ("T-045", "1002 保温杯不想要了", "1002", "refund_request", "refund_apply", "no_reason", ["returns.md#1"], None, "未发货，全额退 89", ()),
    ("T-046", "1010 手机不想要了", "1010", "refund_request", "refund_apply", "no_reason", ["returns.md#5"], None, "未发货金额 699，需人工", ()),
    ("T-047", "1003 键盘不想要了", "1003", "refund_request", "refund_apply", "no_reason", ["returns.md#5"], None, "已发货，需拦截，转人工", ()),
    ("T-048", "1011 羽绒服退款", "1011", "refund_request", "refund_apply", "no_reason", ["returns.md#5"], None, "已发货，转人工", ()),
    ("T-049", "1008 充电宝退款", "1008", "refund_request", "refund_reject", None, ["returns.md#1"], None, "已取消订单，无款可退", ()),
    ("T-050", "1009 音箱退款", "1009", "refund_request", "refund_reject", None, ["returns.md#1"], None, "已退款订单，不能重复退", ()),
    ("T-051", "1012 背包退款", "1012", "refund_request", "refund_reject", None, [], None, "未支付，引导取消", ()),
    ("T-052", "1004 少发了一个配件", "1004", "refund_request", "refund_apply", "missing", ["returns.md#2", "returns.md#5"], None, "漏发，金额 699 需人工", ()),
    ("T-053", "1003 发错货了", "1003", "refund_request", "refund_apply", "wrong_item", ["returns.md#2"], None, "已发货错发，转人工", ()),
    ("T-054", "1001 屏幕碎了", "1001", "refund_request", "refund_apply", "damaged", ["returns.md#2"], None, "破损，平台承担运费", ()),
    ("T-055", "1006 退货退款，运费谁出？", "1006", "policy_qa", "answer", None, ["shipping.md#7"], None, "质量问题平台承担；无理由用户承担", ()),
    ("T-056", "退款多久到账？", None, "policy_qa", "handoff", None, [], "policy_missing", "知识库未覆盖到账时效；应补知识或转人工", ()),
    ("T-057", "1004 只退一半可以吗？", "1004", "refund_request", "handoff", None, [], "partial_refund", "部分退款规则未定义，转人工", ()),
    ("T-058", "1002 退款要扣运费吗？", "1002", "policy_qa", "answer", None, ["shipping.md#7"], None, "未发货全额退；无理由运费用户承担", ()),
    ("T-059", "1001 耳机有质量问题，申请退款", "1001", "refund_request", "refund_apply", "quality", ["returns.md#2"], None, "15 天内质量问题", ()),
    ("T-060", "1006 想退货", "1006", "after_sales", "clarify", None, ["returns.md#1"], None, "缺退货原因/商品状态，先澄清", ()),
    # 投诉/紧急/人工/范围外
    ("T-061", "1006 你们客服态度太差了，我要投诉", "1006", "complaint", "handoff", None, [], "complaint", "投诉转人工，先共情", ()),
    ("T-062", "1004 再不处理我就投诉", "1004", "complaint", "handoff", None, [], "complaint", "升级投诉，转人工", ()),
    ("T-063", "转人工", None, "human_request", "handoff", None, [], "human_request", "客户明确要求人工", ()),
    ("T-064", "我要人工客服", None, "human_request", "handoff", None, [], "human_request", "客户明确要求人工", ()),
    ("T-065", "1002 我被骗了，救命", "1002", "emergency", "handoff", None, [], "emergency", "紧急/安全，立即转人工", ()),
    ("T-066", "1001 我的账号被盗了", "1001", "emergency", "handoff", None, [], "emergency", "账号安全，立即转人工", ()),
    ("T-067", "1003 别人的订单能帮我查吗？", None, "out_of_scope", "answer", None, [], None, "拒绝越权查询，说明隐私边界", ()),
    ("T-068", "你们老板电话多少？", None, "out_of_scope", "answer", None, [], None, "拒绝提供个人信息，给官方渠道", ()),
    ("T-069", "1001 能改地址吗？", "1001", "after_sales", "handoff", None, [], "unsupported_action", "改地址不在 P0 范围，转人工", ()),
    ("T-070", "1006 能开发票吗？", "1006", "policy_qa", "answer", None, ["faq.md#4"], None, "电子发票可申请，企业抬头转人工", ()),
    # 澄清/闲聊
    ("T-071", "那个怎么样？", None, "needs_clarify", "clarify", None, [], None, "指代不明，列最近订单/商品候选", ()),
    ("T-072", "这个能退吗？", None, "needs_clarify", "clarify", None, [], None, "缺订单与原因，先澄清", ()),
    ("T-073", "你好", None, "chitchat", "answer", None, [], None, "简短问候并给可做的事", ()),
    ("T-074", "你是谁？", None, "chitchat", "answer", None, [], None, "说明是自营商城客服助手", ()),
    ("T-075", "今天天气怎么样？", None, "out_of_scope", "answer", None, [], None, "范围外，礼貌说明并给可做的事", ()),
]


def facts(order_no: str | None) -> dict | None:
    if not order_no:
        return None
    o = ORDERS[order_no]
    return {
        "order_no": order_no,
        "title": o["title"],
        "status": o["status"],
        "paid": o["paid"],
        "goods": o["goods"],
        "shipping": o["shipping"],
        "delivered_days_ago": o["days"],
        "category": o["category"],
        "returnable": o["returnable"],
        "region": o["region"],
    }


def build() -> list[dict]:
    rows = []
    for index, (tid, message, order_no, intent, action, reason, refs, handoff, notes, history) in enumerate(TICKETS):
        expected = {
            "intent": intent,
            "action": action,
            "amount": None,
            "reason_code": reason,
            "requires_human": bool(handoff),
            "handoff_reason": handoff,
            "policy_refs": list(refs),
            "notes": notes,
        }
        if action in {"refund_apply", "refund_reject"}:
            eligible, amount, human, why = refund_decision(order_no, reason or "no_reason")
            expected["amount"] = amount if eligible else 0.0
            expected["requires_human"] = human or bool(handoff)
            expected["notes"] = f"{notes}；规则：{why}"
            if action == "refund_apply" and not eligible:
                raise SystemExit(f"{tid}: 标为 refund_apply 但规则判为不可退（{why}）")
            if action == "refund_reject" and eligible and not human:
                raise SystemExit(f"{tid}: 标为 refund_reject 但规则可直接退")
        rows.append({
            "id": tid,
            "split": "dev" if index % 3 else "holdout",
            "source": "synthetic",
            "scenario_date": SCENARIO_DATE,
            "channel": "web",
            "history": [{"role": role, "content": content} for role, content in history],
            "message": message,
            "order_no": order_no,
            "facts": facts(order_no),
            "expected": expected,
        })
    return rows


def markdown(rows: list[dict]) -> str:
    lines = [
        "# 脱敏工单数据集（合成）",
        "",
        f"> 场景日期：{SCENARIO_DATE}。共 {len(rows)} 条；`dev` {sum(1 for r in rows if r['split'] == 'dev')} 条，`holdout` {sum(1 for r in rows if r['split'] == 'holdout')} 条。",
        "> 全部为合成数据，不含真实个人信息；期望值由 `app/refunds.py` 与 `data/knowledge/*.md` 推导。",
        "",
        "| ID | 消息 | 订单 | 意图 | 动作 | 金额 | 人工 | 依据 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        e = row["expected"]
        amount = "—" if e["amount"] is None else f"{e['amount']:.2f}"
        refs = "、".join(e["policy_refs"]) or "—"
        lines.append(
            f"| {row['id']} | {row['message']} | {row['order_no'] or '—'} | {e['intent']} | {e['action']} | {amount} | {'是' if e['requires_human'] else '否'} | {refs} |"
        )
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    rows = build()
    OUT_JSONL.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSONL.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")
    OUT_MD.write_text(markdown(rows), encoding="utf-8")
    print(json.dumps({"rows": len(rows), "dev": sum(1 for r in rows if r["split"] == "dev"), "holdout": sum(1 for r in rows if r["split"] == "holdout")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
