"""GeminiImageProvider 測試（Vertex generateContent，mock transport）。

spike 實測（2026-08-16）：
- 端點 /publishers/google/models/{model}:generateContent，OAuth Bearer
- generationConfig 必須 responseModalities ["TEXT","IMAGE"]（只給 IMAGE 會被 block）
- imageConfig.aspectRatio（"9:16"/"1:1"…）+ imageSize（"1K"/"2K"；2K 9:16 = 1536×2752）
- 回應 candidates[0].content.parts[].inlineData.data（base64 PNG）
"""

import base64
import json

import httpx
import pytest

from app.services.image.client import GeminiImageProvider, ImageGenerationError

FAKE_PNG = b"\x89PNG\r\n\x1a\n" + b"fake"


def make_provider(handler) -> GeminiImageProvider:
    return GeminiImageProvider(
        project_id="proj-1",
        token_provider=lambda: "tok-9",
        transport=httpx.MockTransport(handler),
    )


def ok_response(image_bytes: bytes = FAKE_PNG) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "candidates": [
                {"content": {"parts": [
                    {"text": "here you go"},
                    {"inlineData": {"mimeType": "image/png",
                                    "data": base64.b64encode(image_bytes).decode()}},
                ]}}
            ]
        },
    )


@pytest.mark.asyncio
async def test_posts_generate_content_with_token_and_modalities():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("authorization")
        captured["payload"] = json.loads(request.read())
        return ok_response()

    provider = make_provider(handler)
    data = await provider.generate("a lake", model="gemini-3.1-flash-image")

    assert data == FAKE_PNG
    assert captured["url"].endswith(
        "/projects/proj-1/locations/global/publishers/google/models/"
        "gemini-3.1-flash-image:generateContent"
    )
    assert captured["auth"] == "Bearer tok-9"
    config = captured["payload"]["generationConfig"]
    assert config["responseModalities"] == ["TEXT", "IMAGE"]
    assert captured["payload"]["contents"][0]["parts"][0]["text"] == "a lake"


@pytest.mark.asyncio
async def test_dimensions_map_to_aspect_ratio_and_size():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["config"] = json.loads(request.read())["generationConfig"]["imageConfig"]
        return ok_response()

    provider = make_provider(handler)
    # hero 1152×2048 → 9:16 + 2K（高解析度）
    await provider.generate("x", model="m", width=1152, height=2048)
    assert captured["config"] == {"aspectRatio": "9:16", "imageSize": "2K"}

    # 無尺寸（裝飾方圖）→ 1:1 + 1K
    await provider.generate("x", model="m")
    assert captured["config"] == {"aspectRatio": "1:1", "imageSize": "1K"}


@pytest.mark.asyncio
async def test_blocked_prompt_raises():
    provider = make_provider(
        lambda r: httpx.Response(200, json={"promptFeedback": {"blockReason": "OTHER"}})
    )
    with pytest.raises(ImageGenerationError) as exc:
        await provider.generate("x", model="m")
    assert "block" in str(exc.value).lower() or "candidates" in str(exc.value)


@pytest.mark.asyncio
async def test_text_only_response_raises():
    provider = make_provider(
        lambda r: httpx.Response(
            200, json={"candidates": [{"content": {"parts": [{"text": "sorry"}]}}]}
        )
    )
    with pytest.raises(ImageGenerationError):
        await provider.generate("x", model="m")


@pytest.mark.asyncio
async def test_http_error_raises():
    provider = make_provider(lambda r: httpx.Response(429, json={"error": {"code": 429}}))
    with pytest.raises(ImageGenerationError) as exc:
        await provider.generate("x", model="m")
    assert "429" in str(exc.value)


@pytest.mark.asyncio
async def test_steps_ignored():
    # 介面相容：CF 的 steps 參數對 Gemini 無意義，靜默忽略
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.read())
        assert "steps" not in json.dumps(payload)
        return ok_response()

    await make_provider(handler).generate("x", model="m", steps=8)
