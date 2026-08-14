"""LLM 抽象層（migration-plan 4.3/4.4）：provider 抽象、任務路由、軟失敗包裝。

全部用 httpx.MockTransport，不打真實 API。
"""

import json

import httpx
import pytest

from app.services.llm.client import (
    LLMClient,
    LLMProvider,
    LLMResult,
    OpenAICompatProvider,
    call_and_parse,
)


def make_provider(handler) -> OpenAICompatProvider:
    return OpenAICompatProvider(
        base_url="https://fake.example/v1",
        api_key="test-key",
        transport=httpx.MockTransport(handler),
    )


def chat_response(content: str) -> httpx.Response:
    return httpx.Response(
        200, json={"choices": [{"message": {"role": "assistant", "content": content}}]}
    )


class TestOpenAICompatProvider:
    @pytest.mark.asyncio
    async def test_sends_openai_chat_payload(self):
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            captured["auth"] = request.headers.get("authorization")
            captured["body"] = json.loads(request.content)
            return chat_response('{"summary": "hi"}')

        provider = make_provider(handler)
        out = await provider.chat("system 說明", "user 內容", max_tokens=1000, model="model-x")

        assert out == '{"summary": "hi"}'
        assert captured["url"] == "https://fake.example/v1/chat/completions"
        assert captured["auth"] == "Bearer test-key"
        assert captured["body"]["model"] == "model-x"
        assert captured["body"]["max_tokens"] == 1000
        assert captured["body"]["messages"] == [
            {"role": "system", "content": "system 說明"},
            {"role": "user", "content": "user 內容"},
        ]

    @pytest.mark.asyncio
    async def test_json_mode_sets_response_format(self):
        captured = {}

        def handler(request):
            captured["body"] = json.loads(request.content)
            return chat_response("{}")

        provider = make_provider(handler)
        await provider.chat("s", "u", max_tokens=10, model="m", json_mode=True)
        assert captured["body"]["response_format"] == {"type": "json_object"}

    @pytest.mark.asyncio
    async def test_content_parts_for_vision(self):
        captured = {}

        def handler(request):
            captured["body"] = json.loads(request.content)
            return chat_response("{}")

        parts = [
            {"type": "text", "text": "看一下"},
            {"type": "image_url", "image_url": {"url": "https://x/1.png"}},
        ]
        provider = make_provider(handler)
        await provider.chat("s", "", max_tokens=10, model="m", content_parts=parts)
        assert captured["body"]["messages"][1] == {"role": "user", "content": parts}

    @pytest.mark.asyncio
    async def test_http_error_raises(self):
        provider = make_provider(lambda req: httpx.Response(429, json={"error": "rate"}))
        with pytest.raises(httpx.HTTPStatusError):
            await provider.chat("s", "u", max_tokens=10, model="m")

    @pytest.mark.asyncio
    async def test_empty_choices_raises(self):
        provider = make_provider(lambda req: httpx.Response(200, json={"choices": []}))
        with pytest.raises(Exception):
            await provider.chat("s", "u", max_tokens=10, model="m")


class FakeProvider(LLMProvider):
    def __init__(self, reply: str = "{}"):
        self.reply = reply
        self.calls: list[dict] = []

    async def chat(self, system, user, *, max_tokens, model, content_parts=None, json_mode=False, reasoning_effort=None):
        self.calls.append({"system": system, "user": user, "model": model})
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


class TestLLMClientRouting:
    def make_client(self, a: LLMProvider, b: LLMProvider) -> LLMClient:
        return LLMClient(
            providers={"prov_a": a, "prov_b": b},
            routing={"travel_summary": ("prov_a", "model-a"), "travel_guide": ("prov_b", "model-b")},
        )

    @pytest.mark.asyncio
    async def test_routes_task_to_configured_provider_and_model(self):
        a, b = FakeProvider('{"ok": 1}'), FakeProvider()
        client = self.make_client(a, b)

        out = await client.chat("travel_summary", "sys", "usr", max_tokens=100)

        assert out == '{"ok": 1}'
        assert a.calls[0]["model"] == "model-a"
        assert b.calls == []

    def test_model_for_task(self):
        client = self.make_client(FakeProvider(), FakeProvider())
        assert client.model_for("travel_summary") == "model-a"

    def test_unknown_task_raises_keyerror(self):
        client = self.make_client(FakeProvider(), FakeProvider())
        with pytest.raises(KeyError):
            client.model_for("nope")


class TestCallAndParse:
    def make_client(self, provider: LLMProvider) -> LLMClient:
        return LLMClient(
            providers={"p": provider}, routing={"travel_summary": ("p", "model-x")}
        )

    @pytest.mark.asyncio
    async def test_success_returns_parsed_data(self):
        client = self.make_client(FakeProvider('```json\n{"summary": "嗨"}\n```'))
        result = await call_and_parse(client, "travel_summary", "s", "u", max_tokens=100)
        assert result == LLMResult(data={"summary": "嗨"}, fail_reason=None)

    @pytest.mark.asyncio
    async def test_provider_exception_becomes_fail_reason_not_raise(self):
        client = self.make_client(FakeProvider())
        client._providers["p"].reply = RuntimeError("boom")

        result = await call_and_parse(client, "travel_summary", "s", "u", max_tokens=100)

        assert result.data is None
        assert result.fail_reason is not None
        assert "RuntimeError" in result.fail_reason
        assert "boom" in result.fail_reason

    @pytest.mark.asyncio
    async def test_unparseable_json_becomes_fail_reason(self):
        client = self.make_client(FakeProvider("這不是 JSON"))
        result = await call_and_parse(client, "travel_summary", "s", "u", max_tokens=100)
        assert result.data is None
        assert "JSON" in result.fail_reason


class TestReasoningEffort:
    @pytest.mark.asyncio
    async def test_provider_sends_reasoning_effort_when_set(self):
        captured = {}

        def handler(request):
            captured["body"] = json.loads(request.content)
            return chat_response("{}")

        provider = make_provider(handler)
        await provider.chat("s", "u", max_tokens=10, model="m", reasoning_effort="none")
        assert captured["body"]["reasoning_effort"] == "none"

    @pytest.mark.asyncio
    async def test_provider_omits_reasoning_effort_by_default(self):
        captured = {}

        def handler(request):
            captured["body"] = json.loads(request.content)
            return chat_response("{}")

        provider = make_provider(handler)
        await provider.chat("s", "u", max_tokens=10, model="m")
        assert "reasoning_effort" not in captured["body"]

    @pytest.mark.asyncio
    async def test_client_routes_reasoning_effort_from_task_config(self):
        captured = {}

        def handler(request):
            captured["body"] = json.loads(request.content)
            return chat_response("{}")

        client = LLMClient(
            providers={"p": make_provider(handler)},
            routing={"travel_summary": ("p", "model-x", "none")},
        )
        await client.chat("travel_summary", "s", "u", max_tokens=10)
        assert captured["body"]["reasoning_effort"] == "none"

    @pytest.mark.asyncio
    async def test_two_tuple_routing_still_works(self):
        captured = {}

        def handler(request):
            captured["body"] = json.loads(request.content)
            return chat_response("{}")

        client = LLMClient(
            providers={"p": make_provider(handler)}, routing={"t": ("p", "model-x")}
        )
        await client.chat("t", "s", "u", max_tokens=10)
        assert "reasoning_effort" not in captured["body"]
