from __future__ import annotations

import re
import secrets
from typing import Literal, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from app.db import SessionLocal
from app.intent import classify, extract_order_id, extract_reason, last_order_id, rewrite_query
from app.knowledge import get_kb
from app.models import Order
from app.refunds import RefundDecision, evaluate_refund
from app.services import (
    create_refund,
    create_ticket,
    enqueue_question,
    logistics_summary,
    order_summary,
    recent_orders,
    sign_confirm_token,
)


class State(TypedDict, total=False):
    session_id: str
    turn_id: str
    user_id: str
    trace_id: str
    query: str
    rewritten: str
    history: list[dict]
    summary: str
    intent: str
    order_id: str | None
    reason_code: str | None
    docs: list[dict]
    citations: list[dict]
    answer: str
    status: str
    requires_human: bool
    cancelled: bool
    no_order: bool
    decision: dict
    pending_action: dict
    confirmation: dict
    receipt: dict
    tool_result: dict
    error: str
    quality_flag: str


def _db_order(db, order_id: str, user_id: str) -> Order | None:
    order = db.get(Order, order_id)
    return order if order and order.user_id == user_id else None


def understand(state: State) -> dict:
    query = state.get("query", "")
    history = state.get("history", [])
    intent = classify(query, history)
    order_id = extract_order_id(query) or (last_order_id(history) if any(k in query for k in ("它", "那单", "这单")) else None)
    reason = extract_reason(query)
    return {
        "intent": intent,
        "order_id": order_id,
        "reason_code": reason,
        "rewritten": rewrite_query(query, history),
        "requires_human": False,
        "cancelled": False,
        "no_order": False,
    }


def route(state: State) -> str:
    intent = state.get("intent")
    if intent == "human_handoff" or intent == "complaint":
        return "handoff"
    if intent == "refund_request" or intent == "after_sales":
        return "after_sales"
    if intent in {"order_query", "logistics_query"}:
        return "tools"
    if intent == "product_qa" and any(k in state.get("query", "") for k in ("有货", "库存", "价格", "多少钱")):
        return "product_tool"
    if intent in {"policy_qa", "product_qa"}:
        return "rag"
    if intent == "chitchat":
        return "generate"
    return "handoff"


def resolve_order(state: State) -> dict:
    if state.get("order_id"):
        return {}
    with SessionLocal() as db:
        rows = recent_orders(db, state["user_id"], limit=8)
        options = [order_summary(o) for o in rows if o.status not in {"cancelled", "refunded", "refunding", "pending_payment"}]
    if not options:
        return {"no_order": True}
    value = interrupt(
        {
            "interrupt_id": f"itp_{secrets.token_hex(8)}",
            "type": "order_select",
            "question": "请选择要处理的订单",
            "options": options,
        }
    )
    if isinstance(value, dict) and value.get("order_id"):
        with SessionLocal() as db:
            order = _db_order(db, str(value["order_id"]), state["user_id"])
            if order is None:
                return {"no_order": True}
        return {"order_id": order.id}
    return {"cancelled": True}


def after_order(state: State) -> str:
    if state.get("cancelled"):
        return "cancel"
    if state.get("no_order"):
        return "generate"
    if state.get("intent") in {"order_query", "logistics_query"} or state.get("intent") == "product_qa":
        return "tool"
    return "after_sales"


def product_tool(state: State) -> dict:
    # 本地 demo：商品服务用静态兜底；真实环境替换为 get_product_info 工具。
    return {"answer": "这款商品目前有货，价格以商品页为准；如果你告诉我具体型号，我可以继续核对规格。", "status": "ok"}


def tool_execute(state: State) -> dict:
    with SessionLocal() as db:
        if state.get("intent") == "product_qa":
            order = _db_order(db, state.get("order_id") or "", state["user_id"])
            # 商品库存/价格在 demo 中用静态商品库兜底；真实环境替换为商品服务。
            answer = "这款商品目前有货，价格以商品页为准。你可以告诉我具体商品名，我再帮你确认规格。"
            return {"answer": answer, "status": "ok"}
        order = _db_order(db, state.get("order_id") or "", state["user_id"])
        if order is None:
            return {"no_order": True}
        if state.get("intent") == "logistics_query":
            info = logistics_summary(order)
            events = "；".join(f"{e.get('time','')} {e.get('text','')}" for e in info.get("events", []))
            answer = f"订单 {order.id} 当前物流状态：{info.get('status')}。{events}".strip()
        else:
            answer = f"订单 {order.id} 当前状态：{order.status}，商品：{order.title}，实付 {order.paid_amount:.2f} 元。"
        return {"answer": answer, "status": "ok"}


def rag(state: State) -> dict:
    query = state.get("rewritten") or state.get("query", "")
    intent = state.get("intent")
    category = None
    if intent == "policy_qa":
        if any(k in query for k in ("邮费", "运费", "包邮", "配送", "会员", "积分")):
            category = "shipping"
        elif any(k in query for k in ("退", "售后", "换")):
            category = "returns"
    if intent == "product_qa":
        category = "faq"
    docs = get_kb().retrieve(query, category=category, top_k=8 if category in {"shipping", "returns"} else 5)
    if not docs:
        return {"status": "refused", "answer": "暂未查到明确规则，我先帮你转人工确认。"}
    citations = [
        {
            "index": i + 1,
            "chunk_id": d.id,
            "title": d.title,
            "source": d.source,
            "effective_from": "2026-01-01",
            "content": d.content,
        }
        for i, d in enumerate(docs)
    ]
    lines = ["根据平台规则："]
    for i, doc in enumerate(docs, 1):
        body = doc.content.split("【正文】", 1)[-1].strip().replace("\n", " ")
        if len(body) > 160:
            body = body[:160] + "…"
        lines.append(f"- {body} [{i}]")
    return {"docs": [d.__dict__ for d in docs], "citations": citations, "answer": "\n".join(lines), "status": "ok"}


def after_sales_check(state: State) -> dict:
    with SessionLocal() as db:
        order = _db_order(db, state.get("order_id") or "", state["user_id"])
        if order is None:
            return {"no_order": True}
        decision = evaluate_refund(order, state.get("reason_code") or "other")
    payload = {
        "eligible": decision.eligible,
        "reason_code": decision.reason_code,
        "reasons": decision.reasons,
        "amount": decision.amount,
        "amount_breakdown": decision.amount_breakdown,
        "shipping_payer": decision.shipping_payer,
        "requires_human": decision.requires_human,
        "policy_snapshot": decision.policy_snapshot,
    }
    if decision.requires_human:
        return {"decision": payload, "requires_human": True, "status": "handoff"}
    if not decision.eligible:
        reasons = "；".join(decision.reasons)
        return {"decision": payload, "answer": f"这笔订单暂时无法直接退款：{reasons}。如需申诉，我可以帮你转人工。", "status": "refused"}
    return {"decision": payload, "pending_action": payload}


def confirm_action(state: State) -> dict:
    action = state.get("pending_action") or {}
    with SessionLocal() as db:
        token, _ = sign_confirm_token(
            db,
            user_id=state["user_id"],
            session_id=state["session_id"],
            order_id=state.get("order_id") or "",
            reason_code=str(action.get("reason_code")),
            amount=float(action.get("amount", 0)),
            policy_hash=str(hash(str(action.get("policy_snapshot")))),
        )
    value = interrupt(
        {
            "interrupt_id": f"itp_{secrets.token_hex(8)}",
            "type": "confirm_refund",
            "question": "确认提交退款申请吗？",
            "order_id": state.get("order_id"),
            "amount": action.get("amount"),
            "reason": action.get("reason_code"),
            "shipping_payer": action.get("shipping_payer"),
            "confirm_token": token,
        }
    )
    if isinstance(value, dict) and value.get("confirmed"):
        return {"confirmation": {"confirmed": True, "confirm_token": value.get("confirm_token") or token}}
    return {"cancelled": True}


def execute_refund(state: State) -> dict:
    action = state.get("pending_action") or {}
    token = (state.get("confirmation") or {}).get("confirm_token")
    with SessionLocal() as db:
        order = _db_order(db, state.get("order_id") or "", state["user_id"])
        if order is None:
            return {"answer": "订单不存在或无权访问。", "status": "handoff", "requires_human": True}
        decision = RefundDecision(
            eligible=True,
            reason_code=str(action.get("reason_code")),
            reasons=list(action.get("reasons", [])),
            amount=float(action.get("amount", 0)),
            amount_breakdown=dict(action.get("amount_breakdown", {})),
            shipping_payer=str(action.get("shipping_payer")),
            requires_human=False,
            policy_snapshot=dict(action.get("policy_snapshot", {})),
        )
        try:
            refund = create_refund(db, order, decision, token or "", state["user_id"], state["session_id"])
        except ValueError:
            return {"answer": "确认已过期或无效，请重新发起退款。", "status": "cancelled"}
    return {
        "receipt": {"refund_id": refund.id, "amount": refund.amount, "status": refund.status, "next_steps": "退款申请已提交，可在订单详情查看进度。"},
        "answer": f"已提交退款申请：退款单 {refund.id}，金额 {refund.amount:.2f} 元，当前状态 {refund.status}。",
        "status": "action_done",
    }


def generate(state: State) -> dict:
    if state.get("answer"):
        return {}
    if state.get("no_order"):
        return {"answer": "没有查到你的订单，请确认订单号，或到订单页查看。", "status": "refused"}
    intent = state.get("intent")
    if intent == "chitchat":
        return {"answer": "你好，我是小猫智能客服，可以帮你查订单、物流、退换货和平台规则。", "status": "ok"}
    return {"answer": "我先帮你转人工确认一下。", "status": "handoff"}


def handoff(state: State) -> dict:
    context = {
        "intent": state.get("intent"),
        "order_id": state.get("order_id"),
        "query": state.get("query"),
        "decision": state.get("decision"),
    }
    with SessionLocal() as db:
        ticket = create_ticket(db, state["session_id"], state["user_id"], "handoff", context)
        enqueue_question(
            db,
            question=state.get("query", ""),
            session_id=state["session_id"],
            message_id=None,
            user_id=state["user_id"],
            source_type="handoff",
        )
    return {"answer": f"已为你转接人工客服（工单 {ticket.id}），请稍候。", "status": "handoff"}


def cancel_ack(state: State) -> dict:
    return {"answer": "好的，本次操作已取消。如果还需要帮助，随时告诉我。", "status": "cancelled"}


def build_graph(checkpointer):
    builder = StateGraph(State)
    builder.add_node("understand", understand)
    builder.add_node("resolve_order", resolve_order)
    builder.add_node("tool_execute", tool_execute)
    builder.add_node("product_tool", product_tool)
    builder.add_node("rag", rag)
    builder.add_node("after_sales_check", after_sales_check)
    builder.add_node("confirm_action", confirm_action)
    builder.add_node("execute_refund", execute_refund)
    builder.add_node("generate", generate)
    builder.add_node("handoff", handoff)
    builder.add_node("cancel_ack", cancel_ack)

    builder.add_edge(START, "understand")
    builder.add_conditional_edges(
        "understand",
        route,
        {"rag": "rag", "tools": "resolve_order", "product_tool": "product_tool", "after_sales": "resolve_order", "handoff": "handoff", "generate": "generate"},
    )
    builder.add_edge("rag", "generate")
    builder.add_edge("product_tool", "generate")
    builder.add_conditional_edges(
        "resolve_order",
        after_order,
        {"cancel": "cancel_ack", "generate": "generate", "tool": "tool_execute", "after_sales": "after_sales_check"},
    )
    builder.add_edge("tool_execute", "generate")
    builder.add_conditional_edges(
        "after_sales_check",
        lambda s: "handoff" if s.get("requires_human") else ("confirm" if s.get("pending_action") else "generate"),
        {"handoff": "handoff", "confirm": "confirm_action", "generate": "generate"},
    )
    builder.add_conditional_edges(
        "confirm_action",
        lambda s: "cancel" if s.get("cancelled") else "execute",
        {"cancel": "cancel_ack", "execute": "execute_refund"},
    )
    builder.add_edge("execute_refund", "generate")
    builder.add_edge("generate", END)
    builder.add_edge("handoff", END)
    builder.add_edge("cancel_ack", END)
    return builder.compile(checkpointer=checkpointer)
