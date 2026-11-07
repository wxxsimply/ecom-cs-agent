from __future__ import annotations

import re
import secrets
from typing import Literal, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from app import llm
from app.db import SessionLocal
from app.intent import classify, extract_order_id, extract_reason, last_order_id, rewrite_query
from app.knowledge import get_kb
from app.models import Order
from app.refunds import RefundDecision, evaluate_refund
from app.session_state import is_new_topic, load_state, match_pending, pending_expired
from app.services import (
    create_refund,
    create_ticket,
    enqueue_question,
    logistics_summary,
    order_summary,
    recent_orders,
    sign_confirm_token,
)

CAPABILITIES = (
    "我可以帮你：\n"
    "- 查询订单状态和物流进度\n"
    "- 办理退换货/退款申请\n"
    "- 解答运费、会员、发票等平台规则\n"
    "- 转接人工客服"
)


def _scope_boundary(query: str) -> str:
    q = query or ""
    if any(k in q for k in ("天气", "新闻", "股票", "彩票")):
        return "我无法读取实时天气/新闻/行情数据，也不能提供投资建议。"
    if any(k in q for k in ("代码", "程序", "论文", "翻译")):
        return "我不能代写代码/论文或执行程序，也不能替代专业翻译。"
    if any(k in q for k in ("头疼", "药", "病", "医生", "治疗")):
        return "我不能做医疗诊断或推荐药物，这类问题需要专业医生判断。"
    if any(k in q for k in ("笑话", "故事", "聊天")):
        return "我主要处理商城客服问题，不适合长时间闲聊。"
    if any(k in q for k in ("别人", "他人", "其他人的订单")):
        return "出于隐私和账号安全，我不能查询或操作他人的订单、退款或银行卡。"
    if any(k in q for k in ("改地址", "改收货地址")):
        return "已支付订单的地址修改受支付/物流状态限制，我不能直接承诺修改成功，但可以帮你确认条件并记录处理。"
    return "这件事超出我当前能直接办理的范围，我不能编造答案或承诺结果。"


class State(TypedDict, total=False):
    session_id: str
    turn_id: str
    user_id: str
    trace_id: str
    query: str
    rewritten: str
    history: list[dict]
    summary: str
    session_state: dict
    pending_slot: dict | None
    pending_cleared: bool
    last_order_id: str | None
    asked_question: str | None
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
    progress: dict
    refusal_code: str | None
    error: str
    quality_flag: str


def _db_order(db, order_id: str, user_id: str) -> Order | None:
    order = db.get(Order, order_id)
    return order if order and order.user_id == user_id else None


def _is_obviously_out_of_scope(query: str) -> bool:
    return any(
        keyword in (query or "")
        for keyword in (
            "天气",
            "新闻",
            "股票",
            "彩票",
            "代码",
            "程序",
            "论文",
            "翻译",
            "头疼",
            "药",
            "病",
            "医生",
            "治疗",
            "笑话",
            "故事",
            "聊天",
            "别人",
            "他人",
            "改地址",
        )
    )


def understand(state: State) -> dict:
    query = state.get("query", "")
    history = state.get("history", [])
    session_state = load_state(state.get("session_state"))
    pending = session_state.get("pending")
    pending_match = match_pending(session_state, query)
    if pending_match:
        if pending_match["field"] == "reason_code":
            return {
                "intent": "refund_request",
                "order_id": state.get("order_id") or session_state.get("last_order_id") or last_order_id(history),
                "reason_code": pending_match["value"],
                "rewritten": rewrite_query(query, history),
                "pending_cleared": True,
                "pending_slot": None,
                "requires_human": False,
                "cancelled": False,
                "no_order": False,
                "session_state": session_state,
            }
        if pending_match["field"] == "order_id":
            return {
                "intent": "refund_request",
                "order_id": pending_match["value"],
                "reason_code": extract_reason(query),
                "rewritten": rewrite_query(query, history),
                "pending_cleared": True,
                "pending_slot": None,
                "requires_human": False,
                "cancelled": False,
                "no_order": False,
                "session_state": session_state,
            }

    pending_cleared = False
    if pending:
        if pending_expired(pending):
            pending_cleared = True
            pending = None
        elif is_new_topic(query):
            pending_cleared = True
            pending = None
        else:
            attempts = int(pending.get("attempts", 1))
            if pending.get("type") == "reason_select" and attempts >= 2:
                return {
                    "intent": "human_request",
                    "order_id": session_state.get("last_order_id") or last_order_id(history),
                    "reason_code": None,
                    "rewritten": rewrite_query(query, history),
                    "pending_cleared": True,
                    "pending_slot": None,
                    "requires_human": True,
                    "cancelled": False,
                    "no_order": False,
                    "session_state": session_state,
                }
            uses_known_order = pending.get("type") != "order_select"
            return {
                "intent": "refund_request",
                "order_id": (session_state.get("last_order_id") or last_order_id(history)) if uses_known_order else None,
                "reason_code": None,
                "rewritten": rewrite_query(query, history),
                "requires_human": False,
                "cancelled": False,
                "no_order": False,
                "session_state": session_state,
            }

    intent = classify(query, history)
    rewritten = rewrite_query(query, history)
    if intent in {"out_of_scope", "needs_clarify"} and history and not _is_obviously_out_of_scope(query):
        llm_query = llm.rewrite_followup(query, history, session_state, state.get("summary", ""))
        if llm_query:
            candidate = classify(llm_query, history)
            if candidate != "out_of_scope":
                intent = candidate
                rewritten = llm_query
    order_id = extract_order_id(query)
    if not order_id and (pending or intent in {"order_query", "logistics_query", "refund_request", "after_sales"}):
        order_id = session_state.get("last_order_id")
    if not order_id and any(k in query for k in ("它", "那单", "这单")):
        order_id = last_order_id(history)
    reason = extract_reason(query)
    return {
        "intent": intent,
        "order_id": order_id,
        "reason_code": reason,
        "rewritten": rewritten,
        "requires_human": False,
        "cancelled": False,
        "no_order": False,
        "pending_cleared": pending_cleared,
        "pending_slot": None,
        "session_state": session_state,
    }


def route(state: State) -> str:
    intent = state.get("intent")
    if intent == "emergency":
        return "emergency"
    if intent in {"human_request", "human_handoff"}:
        return "human_request"
    if intent == "complaint":
        return "complaint"
    if intent == "refund_request" or intent == "after_sales":
        return "after_sales"
    if intent in {"order_query", "logistics_query"}:
        return "tools"
    if intent == "product_qa" and any(k in state.get("query", "") for k in ("有货", "库存", "价格", "多少钱")):
        return "product_tool"
    if intent in {"policy_qa", "product_qa"}:
        return "rag"
    if intent == "general_qa":
        return "general_qa"
    if intent == "needs_clarify":
        return "clarify"
    if intent == "chitchat":
        return "generate"
    if intent == "out_of_scope":
        return "out_of_scope"
    return "human_request"


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
            return {"no_order": True, "pending_cleared": True, "pending_slot": None}
        if state.get("intent") == "logistics_query":
            info = logistics_summary(order)
            lines = [f"订单 {order.id} 当前物流状态：{info.get('status')}", ""]
            events = info.get("events", [])
            if events:
                for event in events:
                    lines.append(f"- {event.get('time', '')} {event.get('text', '')}".strip())
            else:
                lines.append("- 暂无物流轨迹")
            answer = "\n".join(lines).strip()
            return {"answer": answer, "status": "ok", "progress": info.get("progress")}
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
    if not state.get("reason_code"):
        session_state = load_state(state.get("session_state"))
        pending = session_state.get("pending") or {}
        attempts = int(pending.get("attempts", 0)) + 1 if pending.get("type") == "reason_select" else 1
        return {
            "answer": (
                "可以，我先帮你判断退款/退货资格。请问是以下哪种情况：\n"
                "- 质量问题（坏了/破损/故障）\n"
                "- 发错货/少件/漏发\n"
                "- 不想要了（无理由）\n\n"
                "你回一个关键词即可，我再给你具体金额和下一步。"
            ),
            "status": "ok",
            "pending_slot": {
                "type": "reason_select",
                "field": "reason_code",
                "allowed": ["quality", "wrong_item", "missing", "no_reason", "other"],
                "attempts": attempts,
            },
            "asked_question": "reason_select",
        }
    with SessionLocal() as db:
        order = _db_order(db, state.get("order_id") or "", state["user_id"])
        if order is None:
            return {"no_order": True, "pending_cleared": True, "pending_slot": None}
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
        return {"decision": payload, "requires_human": True, "status": "handoff", "pending_cleared": True, "pending_slot": None}
    if not decision.eligible:
        reasons = "；".join(decision.reasons)
        return {"decision": payload, "answer": f"这笔订单暂时无法直接退款：{reasons}。如需申诉，我可以帮你转人工。", "status": "refused", "pending_cleared": True, "pending_slot": None}
    return {"decision": payload, "pending_action": payload, "pending_cleared": True, "pending_slot": None}


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
        query = state.get("query", "")
        if any(key in query for key in ("你能做什么", "可以做什么", "会什么", "能帮我做什么")):
            return {"answer": f"我是小猫智能客服。\n\n{CAPABILITIES}", "status": "ok"}
        if any(key in query for key in ("机器人", "AI", "ai")):
            return {
                "answer": (
                    "我是小猫 AI 客服，不是真人。\n\n"
                    "我可以查订单/物流、判断退款资格、解释平台规则；"
                    "如果遇到安全、诈骗或高价值异常，可以走紧急人工。"
                ),
                "status": "ok",
            }
        return {"answer": "你好，我是小猫智能客服，可以帮你查订单、物流、退换货和平台规则。", "status": "ok"}
    return {"answer": "我先帮你转人工确认一下。", "status": "handoff"}


def out_of_scope(state: State) -> dict:
    query = state.get("query", "")
    boundary = _scope_boundary(query)
    answer = (
        f"我理解你想问的是「{query}」。\n\n"
        f"{boundary}\n\n"
        "不过我可以继续帮你：\n"
        "- 查订单/物流进度\n"
        "- 判断退款/退货资格并提交申请\n"
        "- 查运费、会员、积分、发票等平台规则\n\n"
        "你可以直接说“查订单 1001”“我要退款”或“邮费多少”，我马上处理。"
    )
    return {"answer": answer, "status": "refused", "refusal_code": "out_of_scope"}


def complaint(state: State) -> dict:
    query = state.get("query", "")
    answer = (
        f"我理解你对「{query}」的体验不满意，先向你道歉。\n\n"
        "我可以先帮你把问题落到具体处理上：\n"
        "- 如果是订单/物流问题，我可以立刻查当前状态\n"
        "- 如果是退款/售后问题，我可以先判断资格和金额\n"
        "- 如果确实需要人工复核，我会记录工单，不让问题停在“已转人工”\n\n"
        "请告诉我订单号或具体发生了什么，我先处理第一步。"
    )
    return {"answer": answer, "status": "ok"}


def human_request(state: State) -> dict:
    context = {"intent": "human_request", "query": state.get("query"), "order_id": state.get("order_id")}
    with SessionLocal() as db:
        ticket = create_ticket(db, state["session_id"], state["user_id"], "async_handoff", context)
        enqueue_question(
            db,
            question=state.get("query", ""),
            session_id=state["session_id"],
            message_id=None,
            user_id=state["user_id"],
            source_type="human_request",
        )
    answer = (
        "可以，我先不让你排队空等。\n\n"
        "你可以先直接说“查订单 1001”“我要退款”或“物流到哪了”，我能马上处理大部分问题。\n\n"
        f"如果仍需人工复核，我已记录异步工单 **{ticket.id}**，会有同事按顺序跟进；"
        "如果事情涉及安全、诈骗、盗刷或人身风险，请直接说“紧急”，我会优先标记。"
    )
    return {"answer": answer, "status": "handoff"}


def emergency(state: State) -> dict:
    context = {"intent": "emergency", "query": state.get("query")}
    with SessionLocal() as db:
        ticket = create_ticket(db, state["session_id"], state["user_id"], "emergency", context)
    answer = (
        "这属于需要优先处理的情况。\n\n"
        "如果涉及诈骗/盗刷，请立即联系银行冻结卡片并拨打 110；如果涉及人身安全，请先确保自身安全并报警。\n\n"
        f"我已创建紧急工单 **{ticket.id}** 并标记优先处理。请再补充订单号/发生了什么，我继续帮你整理信息。"
    )
    return {"answer": answer, "status": "handoff"}


def clarify(state: State) -> dict:
    with SessionLocal() as db:
        rows = recent_orders(db, state["user_id"], limit=3)
        options = [f"订单 {o.id}（{o.title}，{o.status}）" for o in rows]
    option_text = "\n".join(f"- {item}" for item in options) if options else "- 你可以在订单页复制订单号"
    answer = (
        "我理解你在指一个具体对象，但还缺一点信息。\n\n"
        f"你是在问下面哪一个？\n{option_text}\n\n"
        "直接回复订单号，或说“第一个/第二个”，我就按它继续查。"
    )
    return {"answer": answer, "status": "ok"}


def general_qa(state: State) -> dict:
    query = state.get("query", "")
    answer = (
        f"我理解你想了解「{query}」。\n\n"
        "这类问题我可以先给一般性建议：\n"
        "- 先确认使用场景/预算/规格，再比较商品\n"
        "- 涉及平台政策、金额、时效时，以订单页和知识库为准\n"
        "- 如果你告诉我具体商品或订单，我可以查实时信息\n\n"
        "你可以补充商品名/订单号，或直接问“邮费多少”“订单 1001 到哪了”。"
    )
    return {"answer": answer, "status": "ok"}


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
    return {
        "answer": (
            f"这项操作需要人工复核，我已创建异步审核工单 **{ticket.id}**，"
            "不会让你在实时队列里空等；有结果会按工单顺序跟进。"
        ),
        "status": "handoff",
    }


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
    builder.add_node("out_of_scope", out_of_scope)
    builder.add_node("complaint", complaint)
    builder.add_node("human_request", human_request)
    builder.add_node("emergency", emergency)
    builder.add_node("clarify", clarify)
    builder.add_node("general_qa", general_qa)
    builder.add_node("handoff", handoff)
    builder.add_node("cancel_ack", cancel_ack)

    builder.add_edge(START, "understand")
    builder.add_conditional_edges(
        "understand",
        route,
        {"rag": "rag", "tools": "resolve_order", "product_tool": "product_tool", "after_sales": "resolve_order", "handoff": "handoff", "generate": "generate", "out_of_scope": "out_of_scope", "complaint": "complaint", "human_request": "human_request", "emergency": "emergency", "clarify": "clarify", "general_qa": "general_qa"},
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
    builder.add_edge("out_of_scope", END)
    builder.add_edge("complaint", END)
    builder.add_edge("human_request", END)
    builder.add_edge("emergency", END)
    builder.add_edge("clarify", END)
    builder.add_edge("general_qa", END)
    builder.add_edge("handoff", END)
    builder.add_edge("cancel_ack", END)
    return builder.compile(checkpointer=checkpointer)
