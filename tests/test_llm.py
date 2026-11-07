from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from app import llm


@pytest.fixture(autouse=True)
def reset_endpoint_style():
    llm._endpoint_style = None
    yield
    llm._endpoint_style = None


def fake_settings(**overrides):
    values = {
        "llm_mode": "deepseek",
        "deepseek_api_key": "test-key",
        "deepseek_base_url": "https://api.deepseek.com",
        "deepseek_api_style": "auto",
        "deepseek_model": "deepseek-chat",
        "deepseek_timeout_seconds": 1,
        "deepseek_max_tokens": 800,
        "deepseek_summary_max_tokens": 400,
        "summary_max_chars": 1200,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class FakeResponse:
    def __init__(self, status_code: int = 200, payload: dict | None = None):
        self.status_code = status_code
        self.payload = payload or {}
        self.request = httpx.Request("POST", "https://api.deepseek.com/chat/completions")

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}",
                request=self.request,
                response=httpx.Response(self.status_code, request=self.request),
            )

    def json(self):
        return self.payload


class FakeClient:
    def __init__(self, response: FakeResponse):
        self.response = response
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def post(self, url, *, headers, json):
        self.calls.append({"url": url, "headers": headers, "json": json})
        return self.response


class SequenceClient(FakeClient):
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, *, headers, json):
        self.calls.append({"url": url, "headers": headers, "json": json})
        return self.responses.pop(0)


def responses_payload(text: str = "正常") -> dict:
    return {
        "id": "resp_1",
        "model": "deepseek-flash-1017",
        "output": [
            {
                "type": "message",
                "content": [{"type": "output_text", "text": text}],
            }
        ],
        "usage": {"input_tokens": 5, "output_tokens": 2, "total_tokens": 7},
    }


def test_llm_chat_success_parses_content_and_keeps_key_out_of_result(monkeypatch):
    monkeypatch.setattr(llm, "settings", fake_settings())
    fake = FakeClient(
        FakeResponse(
            200,
            {
                "model": "deepseek-chat",
                "choices": [{"message": {"content": "订单 1001 质量问题退款"}}],
                "usage": {"total_tokens": 12},
            },
        )
    )
    monkeypatch.setattr(llm.httpx, "Client", lambda **kwargs: fake)
    result = llm.chat([{"role": "user", "content": "质量问题"}])

    assert result.text == "订单 1001 质量问题退款"
    assert result.degraded is False
    assert fake.calls[0]["headers"]["Authorization"] == "Bearer test-key"
    assert "test-key" not in str(result)


def test_llm_chat_auth_failure_degrades_without_retry(monkeypatch):
    monkeypatch.setattr(llm, "settings", fake_settings())
    fake = FakeClient(FakeResponse(401))
    monkeypatch.setattr(llm.httpx, "Client", lambda **kwargs: fake)
    result = llm.chat([{"role": "user", "content": "hello"}])

    assert result.degraded is True
    assert result.error == "auth_failed"
    assert len(fake.calls) == 1


def test_llm_chat_retries_429_once(monkeypatch):
    monkeypatch.setattr(llm, "settings", fake_settings())
    fake = FakeClient(FakeResponse(429))
    monkeypatch.setattr(llm.httpx, "Client", lambda **kwargs: fake)
    result = llm.chat([{"role": "user", "content": "hello"}])

    assert result.degraded is True
    assert result.error == "http_429"
    assert len(fake.calls) == 2


def test_llm_chat_falls_back_to_responses_on_404(monkeypatch):
    monkeypatch.setattr(llm, "settings", fake_settings())
    fake = SequenceClient([FakeResponse(404), FakeResponse(200, responses_payload())])
    monkeypatch.setattr(llm.httpx, "Client", lambda **kwargs: fake)

    result = llm.chat(
        [
            {"role": "system", "content": "你是客服"},
            {"role": "user", "content": "只回复正常"},
        ],
        max_tokens=16,
    )

    assert result.degraded is False
    assert result.text == "正常"
    assert result.model == "deepseek-flash-1017"
    assert result.usage["total_tokens"] == 7
    assert fake.calls[0]["url"].endswith("/chat/completions")
    assert fake.calls[1]["url"].endswith("/responses")
    assert fake.calls[1]["json"]["max_output_tokens"] == 16
    assert fake.calls[1]["json"]["instructions"] == "你是客服"
    assert fake.calls[1]["json"]["input"] == [{"role": "user", "content": "只回复正常"}]


def test_llm_chat_can_force_responses_style(monkeypatch):
    monkeypatch.setattr(llm, "settings", fake_settings(deepseek_api_style="responses"))
    fake = SequenceClient([FakeResponse(200, responses_payload("好的"))])
    monkeypatch.setattr(llm.httpx, "Client", lambda **kwargs: fake)

    result = llm.chat([{"role": "user", "content": "hello"}])

    assert result.degraded is False
    assert result.text == "好的"
    assert len(fake.calls) == 1
    assert fake.calls[0]["url"].endswith("/responses")


def test_llm_not_configured_returns_structured_degraded(monkeypatch):
    monkeypatch.setattr(llm, "settings", fake_settings(deepseek_api_key=""))
    result = llm.chat([{"role": "user", "content": "hello"}])
    assert result.degraded is True
    assert result.error == "not_configured"
    assert llm.effective_mode() == "degraded"


def test_llm_requires_base_url_and_model(monkeypatch):
    monkeypatch.setattr(llm, "settings", fake_settings(deepseek_base_url=""))
    assert llm.is_configured() is False
    assert llm.effective_mode() == "degraded"
