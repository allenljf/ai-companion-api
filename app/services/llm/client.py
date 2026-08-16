"""LLM 整合層（migration-plan 4.3/4.4；對應原始 AiAgentService + luna/terra 分流）。

- LLMProvider：抽象介面。Gemini / Groq 都提供 OpenAI 相容端點，共用 OpenAICompatProvider
- LLMClient：依任務名稱路由到 (provider, model)，換模型 = 改設定不改程式
- call_and_parse：軟失敗包裝——LLM 拋錯或 JSON 解析失敗回 fail_reason，絕不往上 raise
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass

import httpx

from app.core.json_parse import decode_llm_json

logger = logging.getLogger(__name__)

# travel-guide 要 30s、產圖更久；預設 5s 會全軍覆沒（CLAUDE.md 技術決策）
DEFAULT_TIMEOUT_SECONDS = 90.0


class LLMProvider(ABC):
    @abstractmethod
    async def chat(
        self,
        system: str,
        user: str,
        *,
        max_tokens: int,
        model: str,
        content_parts: list[dict] | None = None,
        json_mode: bool = False,
        reasoning_effort: str | None = None,
    ) -> str:
        """回傳 assistant 的文字內容；任何失敗直接 raise，由 call_and_parse 統一軟失敗。"""


class OpenAICompatProvider(LLMProvider):
    """OpenAI 相容的 chat completions 端點（Gemini / Groq / OpenAI 皆可用）。"""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=httpx.Timeout(timeout),
            transport=transport,
        )

    async def chat(
        self,
        system: str,
        user: str,
        *,
        max_tokens: int,
        model: str,
        content_parts: list[dict] | None = None,
        json_mode: bool = False,
        reasoning_effort: str | None = None,
    ) -> str:
        payload: dict = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": content_parts if content_parts else user},
            ],
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        # reasoning 模型（如 Qwen）的思考 tokens 會吃掉輸出額度導致空回應；
        # 輕量任務指定 "none"/"low" 關掉（對應原始 luna 的 reasoning_effort: low 設計）
        if reasoning_effort:
            payload["reasoning_effort"] = reasoning_effort

        response = await self._client.post("/chat/completions", json=payload)
        response.raise_for_status()
        body = response.json()
        choices = body.get("choices") or []
        if not choices:
            raise ValueError(f"LLM 回應沒有 choices: {body}")
        content = (choices[0].get("message") or {}).get("content")
        if not isinstance(content, str):
            raise ValueError(f"LLM 回應缺 message.content: {body}")
        return content


class VertexAIProvider(LLMProvider):
    """Vertex AI 的 OpenAI 相容端點（2026-08-16 起的主 provider，計費走 GCP 帳單/試用額度）。

    - 認證：OAuth access token（Cloud Run 上由 ADC 走 metadata server 的 service account，
      本地需 `gcloud auth application-default login`）；token provider 可注入供測試
    - 模型名自動補 google/ 前綴；location=global 走全域端點，指定區域走區域 host
    - reasoning_effort 只接受 minimal/low/medium/high（Gemini 3.x 是 thinking 模型，
      不能完全關閉——輕量任務用 minimal 壓思考預算，對應 Groq 時代的 :none）
    """

    def __init__(
        self,
        project_id: str,
        location: str = "global",
        *,
        token_provider=None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        host = (
            "https://aiplatform.googleapis.com"
            if location == "global"
            else f"https://{location}-aiplatform.googleapis.com"
        )
        self._token_provider = token_provider or _AdcTokenProvider()
        self._client = httpx.AsyncClient(
            base_url=f"{host}/v1/projects/{project_id}/locations/{location}/endpoints/openapi",
            timeout=httpx.Timeout(timeout),
            transport=transport,
        )

    async def chat(
        self,
        system: str,
        user: str,
        *,
        max_tokens: int,
        model: str,
        content_parts: list[dict] | None = None,
        json_mode: bool = False,
        reasoning_effort: str | None = None,
    ) -> str:
        payload: dict = {
            "model": model if model.startswith("google/") else f"google/{model}",
            "max_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": content_parts if content_parts else user},
            ],
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        if reasoning_effort:
            payload["reasoning_effort"] = reasoning_effort

        token = self._token_provider()
        if hasattr(token, "__await__"):
            token = await token
        response = await self._client.post(
            "/chat/completions", json=payload, headers={"Authorization": f"Bearer {token}"}
        )
        response.raise_for_status()
        body = response.json()
        choices = body.get("choices") or []
        if not choices:
            raise ValueError(f"LLM 回應沒有 choices: {body}")
        content = (choices[0].get("message") or {}).get("content")
        if not isinstance(content, str):
            raise ValueError(f"LLM 回應缺 message.content: {body}")
        return content


class _AdcTokenProvider:
    """Google ADC access token（有快取，過期自動 refresh；google-auth 是同步的，量小可接受）。"""

    def __init__(self):
        self._credentials = None

    def __call__(self) -> str:
        import google.auth
        from google.auth.transport.requests import Request

        if self._credentials is None:
            self._credentials, _ = google.auth.default(
                scopes=["https://www.googleapis.com/auth/cloud-platform"]
            )
        if not self._credentials.valid:
            self._credentials.refresh(Request())
        return self._credentials.token


class LLMClient:
    """依任務名稱路由到不同 provider/model（對應原始 luna/terra 分流）。"""

    def __init__(self, providers: dict[str, LLMProvider], routing: dict[str, tuple]):
        # routing: {task: (provider, model) 或 (provider, model, reasoning_effort)}
        self._providers = providers
        self._routing = routing

    def _route(self, task: str) -> tuple[str, str, str | None]:
        route = self._routing[task]
        provider_name, model = route[0], route[1]
        reasoning_effort = route[2] if len(route) > 2 else None
        return provider_name, model, reasoning_effort

    def model_for(self, task: str) -> str:
        return self._route(task)[1]

    async def chat(
        self,
        task: str,
        system: str,
        user: str,
        *,
        max_tokens: int,
        content_parts: list[dict] | None = None,
        json_mode: bool = False,
    ) -> str:
        provider_name, model, reasoning_effort = self._route(task)
        return await self._providers[provider_name].chat(
            system,
            user,
            max_tokens=max_tokens,
            model=model,
            content_parts=content_parts,
            json_mode=json_mode,
            reasoning_effort=reasoning_effort,
        )


@dataclass
class LLMResult:
    data: dict | list | None
    fail_reason: str | None


async def call_and_parse(
    client: LLMClient,
    task: str,
    system: str,
    user: str,
    *,
    max_tokens: int,
    content_parts: list[dict] | None = None,
    json_mode: bool = True,
) -> LLMResult:
    """呼叫 LLM 並解析 JSON；失敗一律回 fail_reason（軟失敗，source-spec 技法 3）。"""
    try:
        raw = await client.chat(
            task,
            system,
            user,
            max_tokens=max_tokens,
            content_parts=content_parts,
            json_mode=json_mode,
        )
    except Exception as exc:
        logger.error("LLM failed", extra={"task": task, "error": str(exc)})
        return LLMResult(None, f"LLM fail: {type(exc).__name__}: {exc}")

    parsed = decode_llm_json(raw)
    if parsed is None:
        return LLMResult(None, "LLM fail: LLM 回應無法解析為預期 JSON")
    return LLMResult(parsed, None)
