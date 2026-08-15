"""Image client 測試（Cloudflare Workers AI，mock httpx transport）。

spike 實測結論（2026-08-16）：
- flux-2-klein-4b 只吃 multipart form（JSON 回 400 required 'multipart'），支援 width/height 直式
- flux-1-schnell 吃 JSON，無尺寸參數（固定方圖）
- 兩者都回 JSON {result: {image: <base64>}}，klein 解出來是 JPEG
- 參考圖欄位（image/images/input_image）一律被忽略 → 本抽象不提供參考圖介面
"""

import base64
import json

import httpx
import pytest

from app.services.image.client import CloudflareImageProvider, ImageGenerationError

FAKE_JPEG = b"\xff\xd8\xff\xe0" + b"fakejpegdata"
FAKE_PNG = b"\x89PNG\r\n\x1a\n" + b"fakepngdata"


def make_provider(handler) -> CloudflareImageProvider:
    return CloudflareImageProvider(
        account_id="acct123",
        api_token="tok",
        transport=httpx.MockTransport(handler),
    )


def ok_response(image_bytes: bytes) -> httpx.Response:
    return httpx.Response(
        200, json={"result": {"image": base64.b64encode(image_bytes).decode()}, "success": True}
    )


@pytest.mark.asyncio
async def test_multipart_model_sends_form_with_dimensions():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["content_type"] = request.headers.get("content-type", "")
        captured["body"] = request.read()
        return ok_response(FAKE_JPEG)

    provider = make_provider(handler)
    data = await provider.generate(
        "a kyoto street", model="@cf/black-forest-labs/flux-2-klein-4b",
        width=1152, height=2048,
    )
    assert data == FAKE_JPEG
    assert captured["url"].endswith("/accounts/acct123/ai/run/@cf/black-forest-labs/flux-2-klein-4b")
    # klein 系列走 multipart form（JSON 會 400）
    assert captured["content_type"].startswith("multipart/form-data")
    assert b"a kyoto street" in captured["body"]
    assert b"1152" in captured["body"] and b"2048" in captured["body"]


@pytest.mark.asyncio
async def test_json_model_sends_json_without_dimensions():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["content_type"] = request.headers.get("content-type", "")
        captured["payload"] = json.loads(request.read())
        return ok_response(FAKE_PNG)

    provider = make_provider(handler)
    data = await provider.generate(
        "a stamp", model="@cf/black-forest-labs/flux-1-schnell", steps=8
    )
    assert data == FAKE_PNG
    # schnell 走 JSON、無尺寸參數（固定方圖）
    assert captured["content_type"].startswith("application/json")
    assert captured["payload"] == {"prompt": "a stamp", "steps": 8}


@pytest.mark.asyncio
async def test_http_error_raises_image_generation_error():
    provider = make_provider(lambda request: httpx.Response(429, json={"errors": ["rate"]}))
    with pytest.raises(ImageGenerationError) as exc:
        await provider.generate("x", model="@cf/black-forest-labs/flux-1-schnell")
    assert "429" in str(exc.value)


@pytest.mark.asyncio
async def test_missing_image_field_raises():
    provider = make_provider(
        lambda request: httpx.Response(200, json={"result": {}, "success": True})
    )
    with pytest.raises(ImageGenerationError):
        await provider.generate("x", model="@cf/black-forest-labs/flux-1-schnell")


@pytest.mark.asyncio
async def test_non_image_bytes_raises():
    provider = make_provider(lambda request: ok_response(b"not-an-image"))
    with pytest.raises(ImageGenerationError) as exc:
        await provider.generate("x", model="@cf/black-forest-labs/flux-1-schnell")
    assert "magic" in str(exc.value).lower() or "圖" in str(exc.value)
