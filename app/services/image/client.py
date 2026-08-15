"""圖片生成整合層（階段 8；Cloudflare Workers AI）。

provider 抽象對應 LLM client 的設計（換供應商 = 換實作不改業務邏輯）。

Cloudflare 實測特性（2026-08-16 spike，詳見 docs/research/image-gen-free-tier.md）：
- flux-2-klein-4b：只吃 multipart form（JSON 回 400），支援 width/height → hero 直式 1152×2048
- flux-1-schnell：吃 JSON，無尺寸參數（固定方圖）→ stamp/tag
- 回傳 JSON {result: {image: <base64>}}；klein 解出為 JPEG、schnell 為 JPEG/PNG
- 參考圖欄位一律被忽略 → 不提供參考圖介面（風格一致性走 prompt 文字，migration-plan 3.3 降級 ①②）
"""

from abc import ABC, abstractmethod

import httpx

CF_BASE_URL = "https://api.cloudflare.com/client/v4"
# klein 系列（flux-2-*）走 multipart；其餘（schnell 等）走 JSON
MULTIPART_MODEL_PREFIX = "@cf/black-forest-labs/flux-2-"

JPEG_MAGIC = b"\xff\xd8\xff"
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

DEFAULT_TIMEOUT_SECONDS = 120.0


class ImageGenerationError(RuntimeError):
    """產圖失敗（HTTP 錯誤、回應缺 image、非合法圖檔）；由 service 層 partial-fail 吸收。"""


class ImageProvider(ABC):
    @abstractmethod
    async def generate(
        self, prompt: str, *, model: str,
        width: int | None = None, height: int | None = None, steps: int | None = None,
    ) -> bytes:
        """回傳圖檔 bytes（JPEG 或 PNG）；任何失敗拋 ImageGenerationError。"""


class CloudflareImageProvider(ImageProvider):
    def __init__(
        self,
        account_id: str,
        api_token: str,
        *,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self._client = httpx.AsyncClient(
            base_url=f"{CF_BASE_URL}/accounts/{account_id}/ai/run",
            headers={"Authorization": f"Bearer {api_token}"},
            timeout=httpx.Timeout(timeout),
            transport=transport,
        )

    async def generate(
        self, prompt: str, *, model: str,
        width: int | None = None, height: int | None = None, steps: int | None = None,
    ) -> bytes:
        if model.startswith(MULTIPART_MODEL_PREFIX):
            fields = {"prompt": prompt}
            if width is not None:
                fields["width"] = str(width)
            if height is not None:
                fields["height"] = str(height)
            if steps is not None:
                fields["steps"] = str(steps)
            # klein 系列要求 multipart/form-data（httpx 用 files=(None, value) 強制 multipart）
            response = await self._client.post(
                f"/{model}", files={key: (None, value) for key, value in fields.items()}
            )
        else:
            payload: dict = {"prompt": prompt}
            if steps is not None:
                payload["steps"] = steps
            response = await self._client.post(f"/{model}", json=payload)

        if response.status_code != 200:
            raise ImageGenerationError(
                f"Cloudflare AI HTTP {response.status_code}: {response.text[:300]}"
            )

        import base64

        image_b64 = (response.json().get("result") or {}).get("image")
        if not isinstance(image_b64, str) or not image_b64:
            raise ImageGenerationError("Cloudflare AI 回應缺 result.image 欄位")

        try:
            data = base64.b64decode(image_b64, validate=True)
        except Exception as exc:
            raise ImageGenerationError(f"result.image 不是合法 base64: {exc}") from exc

        if not (data.startswith(JPEG_MAGIC) or data.startswith(PNG_MAGIC)):
            raise ImageGenerationError("產出內容不是合法圖檔（magic bytes 非 JPEG/PNG）")
        return data


def image_content_type(data: bytes) -> str:
    return "image/png" if data.startswith(PNG_MAGIC) else "image/jpeg"


def image_extension(data: bytes) -> str:
    return "png" if data.startswith(PNG_MAGIC) else "jpg"
