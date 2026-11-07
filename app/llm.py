from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

_ENDPOINT_STYLES = ("chat", "responses")
_endpoint_style: str | None = None


@dataclass
class LLMResult:
    text: str = ""
    model: str = ""
    usage: dict[str, Any] = field(default_factory=dict)
    degraded: bool = False
    error: str | None = None


def is_configured() -> bool:
    return (
        settings.llm_mode.strip().lower() == "deepseek"
        and bool(settings.deepseek_api_key)
        and bool(settings.deepseek_base_url)
        and bool(settings.deepseek_model)
    )


def effective_mode() -> str:
    requested = settings.llm_mode.strip().lower()
    if requested != "deepseek":
        return "mock"
    return "deepseek" if is_configured() else "degraded"


def _requested_style() -> str:
    style = str(getattr(settings, "deepseek_api_style", "auto") or "auto").strip().lower()
    return style if style in _ENDPOINT_STYLES else "auto"


def _style_order() -> list[str]:
    requested = _requested_style()
    if requested != "auto":
        return [requested]
    if _endpoint_style == "responses":
        return ["responses"]
    return ["chat", "responses"]


def _chat_payload(
    messages: list[dict[str, str]], max_tokens: int, temperature: float
) -> dict[str, Any]:
    return {
        "model": settings.deepseek_model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }


def _responses_payload(
    messages: list[dict[str, str]], max_tokens: int, temperature: float
) -> dict[str, Any]:
    instructions = "\n\n".join(
        str(message.get("content") or "").strip()
        for message in messages
        if message.get("role") == "system" and str(message.get("content") or "").strip()
    )
    input_messages = [
        {"role": str(message.get("role") or "user"), "content": str(message.get("content") or "")}
        for message in messages
        if message.get("role") != "system"
    ]
    payload: dict[str, Any] = {
        "model": settings.deepseek_model,
        "input": input_messages or "",
        "max_output_tokens": max_tokens,
        "temperature": temperature,
    }
    if instructions:
        payload["instructions"] = instructions
    return payload


def _post(client: httpx.Client, style: str, payload: dict[str, Any]):
    endpoint = "/responses" if style == "responses" else "/chat/completions"
    response = None
    for attempt in range(2):
        response = client.post(
            f"{settings.deepseek_base_url}{endpoint}",
            headers={
                "Authorization": f"Bearer {settings.deepseek_api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
        if response.status_code == 429 and attempt == 0:
            time.sleep(0.4)
            continue
        response.raise_for_status()
        break
    return response


def _parse_response(data: dict[str, Any], style: str) -> LLMResult:
    if style == "responses":
        parts = [
            str(content.get("text") or "")
            for item in data.get("output") or []
            if isinstance(item, dict)
            for content in item.get("content") or []
            if isinstance(content, dict) and content.get("type") in {"output_text", "text"}
        ]
        if not parts and isinstance(data.get("output_text"), str):
            parts.append(data["output_text"])
        text = "".join(parts).strip()
    else:
        text = str(
            (data.get("choices") or [{}])[0].get("message", {}).get("content") or ""
        ).strip()
    return LLMResult(
        text=text,
        model=str(data.get("model") or settings.deepseek_model),
        usage=dict(data.get("usage") or {}),
    )


def chat(
    messages: list[dict[str, str]],
    *,
    max_tokens: int | None = None,
    temperature: float = 0.2,
) -> LLMResult:
    global _endpoint_style

    if not is_configured():
        return LLMResult(degraded=True, error="not_configured")
    token_limit = max_tokens or settings.deepseek_max_tokens
    styles = _style_order()
    try:
        with httpx.Client(timeout=settings.deepseek_timeout_seconds) as client:
            for index, style in enumerate(styles):
                payload = (
                    _responses_payload(messages, token_limit, temperature)
                    if style == "responses"
                    else _chat_payload(messages, token_limit, temperature)
                )
                try:
                    response = _post(client, style, payload)
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code == 404 and index + 1 < len(styles):
                        continue
                    raise
                data = response.json() if response is not None else {}
                _endpoint_style = style
                return _parse_response(data, style)
        return LLMResult(degraded=True, error="unavailable")
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        error = "auth_failed" if status in {401, 403} else f"http_{status}"
        logger.warning("deepseek request failed: %s", error)
        return LLMResult(degraded=True, error=error)
    except (httpx.TimeoutException, httpx.HTTPError) as exc:
        logger.warning("deepseek request unavailable: %s", type(exc).__name__)
        return LLMResult(degraded=True, error="unavailable")
    except (TypeError, ValueError, KeyError) as exc:
        logger.warning("deepseek response invalid: %s", type(exc).__name__)
        return LLMResult(degraded=True, error="invalid_response")
    except Exception as exc:  # pragma: no cover - defensive boundary for a demo app
        logger.warning("deepseek request unexpected failure: %s", type(exc).__name__)
        return LLMResult(degraded=True, error="unexpected")


def _compact_history(history: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for message in history[-8:]:
        role = "用户" if message.get("role") == "user" else "助手"
        content = str(message.get("content") or "").strip().replace("\n", " ")
        if content:
            lines.append(f"{role}：{content[:300]}")
    return "\n".join(lines)


def rewrite_followup(
    query: str,
    history: list[dict[str, Any]],
    state: dict[str, Any] | None = None,
    summary: str = "",
) -> str | None:
    """Rewrite a short follow-up into a standalone query using bounded state.

    The model is only allowed to rewrite; tools and rules still decide facts.
    """
    if not is_configured() or not query.strip():
        return None
    state = state or {}
    context_lines = []
    if summary:
        context_lines.append(f"会话摘要：{summary[:1600]}")
    if state.get("last_order_id"):
        context_lines.append(f"最近订单：{state['last_order_id']}")
    pending = state.get("pending") or {}
    if isinstance(pending, dict) and pending.get("type"):
        context_lines.append(f"待回答：{pending.get('type')}")
    history_text = _compact_history(history)
    if history_text:
        context_lines.append("最近对话：\n" + history_text)
    messages = [
        {
            "role": "system",
            "content": (
                "你是电商客服的查询改写器。只把用户最后一句短回答改写成可独立理解的中文查询，"
                "保留订单号、退款原因和用户约束。不要编造事实、金额、政策或结论。"
                "只输出一行改写结果，不要解释。"
            ),
        },
        {
            "role": "user",
            "content": "\n".join(context_lines + [f"最后一句：{query.strip()}"]),
        },
    ]
    # /responses reasoning models may spend part of the budget before emitting text.
    result = chat(messages, max_tokens=min(settings.deepseek_max_tokens, 800), temperature=0.0)
    if result.degraded or not result.text:
        return None
    rewritten = result.text.strip().strip("“”\"'").replace("\n", " ").strip()
    if not rewritten or len(rewritten) > 240:
        return None
    return rewritten


def summarize_conversation(
    history: list[dict[str, Any]],
    state: dict[str, Any] | None = None,
    old_summary: str = "",
) -> str | None:
    if not is_configured():
        return None
    context = _compact_history(history)
    if not context:
        return None
    messages = [
        {
            "role": "system",
            "content": (
                "你是客服会话摘要器。只压缩对话中已经出现的事实，保留订单号、诉求、退款原因、"
                f"已确认结论和未完成事项。不得新增金额、政策、日期或承诺。输出不超过 {settings.summary_max_chars} 字。"
            ),
        },
        {
            "role": "user",
            "content": f"旧摘要：{old_summary[:1600]}\n\n对话：\n{context}",
        },
    ]
    result = chat(messages, max_tokens=settings.deepseek_summary_max_tokens, temperature=0.0)
    if result.degraded or not result.text:
        return None
    return result.text.strip()[: settings.summary_max_chars]
