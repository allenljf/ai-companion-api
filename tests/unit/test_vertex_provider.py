"""VertexAIProvider 測試（mock transport + 假 token provider）。

Vertex spike 實測（2026-08-16）：
- OpenAI 相容端點 /v1/projects/{p}/locations/global/endpoints/openapi/chat/completions
- 模型名要帶 google/ 前綴
- reasoning_effort 只接受 minimal/low/medium/high（沒有 none）
- response_format json_object 支援
"""

import json

import httpx
import pytest

from app.services.llm.client import VertexAIProvider


def make_provider(handler, token="tok-123") -> VertexAIProvider:
    return VertexAIProvider(
        project_id="proj-1",
        location="global",
        token_provider=lambda: token,
        transport=httpx.MockTransport(handler),
    )


def ok_response(content="好的") -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


@pytest.mark.asyncio
async def test_posts_to_vertex_openapi_endpoint_with_bearer_token():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("authorization")
        captured["payload"] = json.loads(request.read())
        return ok_response("嗨")

    provider = make_provider(handler)
    result = await provider.chat("sys", "user msg", max_tokens=100, model="gemini-3.6-flash")

    assert result == "嗨"
    assert captured["url"] == (
        "https://aiplatform.googleapis.com/v1/projects/proj-1"
        "/locations/global/endpoints/openapi/chat/completions"
    )
    assert captured["auth"] == "Bearer tok-123"
    # 模型名自動補 google/ 前綴
    assert captured["payload"]["model"] == "google/gemini-3.6-flash"
    assert captured["payload"]["messages"][0] == {"role": "system", "content": "sys"}


@pytest.mark.asyncio
async def test_regional_location_uses_regional_host():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return ok_response()

    provider = VertexAIProvider(
        project_id="proj-1", location="asia-east1",
        token_provider=lambda: "t", transport=httpx.MockTransport(handler),
    )
    await provider.chat("s", "u", max_tokens=10, model="gemini-3.6-flash")
    assert captured["url"].startswith("https://asia-east1-aiplatform.googleapis.com/")


@pytest.mark.asyncio
async def test_passes_json_mode_and_reasoning_effort():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["payload"] = json.loads(request.read())
        return ok_response('{"a":1}')

    provider = make_provider(handler)
    await provider.chat(
        "s", "u", max_tokens=10, model="gemini-3.6-flash",
        json_mode=True, reasoning_effort="minimal",
    )
    assert captured["payload"]["response_format"] == {"type": "json_object"}
    assert captured["payload"]["reasoning_effort"] == "minimal"


@pytest.mark.asyncio
async def test_google_prefixed_model_not_double_prefixed():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["payload"] = json.loads(request.read())
        return ok_response()

    provider = make_provider(handler)
    await provider.chat("s", "u", max_tokens=10, model="google/gemini-3.6-flash")
    assert captured["payload"]["model"] == "google/gemini-3.6-flash"


@pytest.mark.asyncio
async def test_http_error_raises():
    provider = make_provider(lambda r: httpx.Response(429, json=[{"error": {"code": 429}}]))
    with pytest.raises(httpx.HTTPStatusError):
        await provider.chat("s", "u", max_tokens=10, model="gemini-3.6-flash")


@pytest.mark.asyncio
async def test_token_provider_called_per_request():
    tokens = iter(["t1", "t2"])
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers["authorization"])
        return ok_response()

    provider = VertexAIProvider(
        project_id="p", location="global",
        token_provider=lambda: next(tokens), transport=httpx.MockTransport(handler),
    )
    await provider.chat("s", "u", max_tokens=10, model="m")
    await provider.chat("s", "u", max_tokens=10, model="m")
    # 每次請求都重新取 token（token provider 自己負責快取/更新）
    assert seen == ["Bearer t1", "Bearer t2"]
