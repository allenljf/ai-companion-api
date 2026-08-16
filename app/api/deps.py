"""依賴注入：LLM client 組裝（provider 抽象 + 每支 API 可獨立指定模型）。"""

from functools import lru_cache

from app.config import settings
from app.services.llm.client import LLMClient, OpenAICompatProvider, VertexAIProvider
from app.storage.kv import InMemoryKV

# Gemini / Groq 都提供 OpenAI 相容端點，共用同一個 provider 實作
GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai"
GROQ_BASE_URL = "https://api.groq.com/openai/v1"


def _parse_route(spec: str) -> tuple[str, str, str | None]:
    """'groq:qwen/qwen3.6-27b:none' → ('groq', 'qwen/qwen3.6-27b', 'none')

    第三段 reasoning_effort 可省略（'gemini:gemini-3.7-flash' → effort None）。
    """
    parts = [p.strip() for p in spec.split(":", 2)]
    provider, model = parts[0], parts[1]
    effort = parts[2] if len(parts) > 2 and parts[2] else None
    return provider, model, effort


@lru_cache(maxsize=1)
def get_llm_client() -> LLMClient:
    providers: dict = {
        # 主 provider：ADC 認證（Cloud Run runtime SA / 本地 gcloud ADC），無金鑰也能註冊，
        # 認證失敗時 chat 拋錯 → call_and_parse 軟失敗
        "vertex": VertexAIProvider(settings.vertex_project_id, settings.vertex_location),
    }
    if settings.gemini_api_key:
        providers["gemini"] = OpenAICompatProvider(GEMINI_BASE_URL, settings.gemini_api_key)
    if settings.groq_api_key:
        providers["groq"] = OpenAICompatProvider(GROQ_BASE_URL, settings.groq_api_key)

    routing = {
        "travel_summary": _parse_route(settings.llm_travel_summary),
        "travel_summary_from_orders": _parse_route(settings.llm_travel_summary_from_orders),
        "travel_summary_from_wish": _parse_route(settings.llm_travel_summary_from_wish),
        "travel_summary_from_history": _parse_route(settings.llm_travel_summary_from_history),
        "recommend_city": _parse_route(settings.llm_recommend_city),
        "travel_guide": _parse_route(settings.llm_travel_guide),
        "travel_revise": _parse_route(settings.llm_travel_revise),
        "quiz": _parse_route(settings.llm_quiz),
        "quiz_completions": _parse_route(settings.llm_quiz_completions),
        "self_introduction": _parse_route(settings.llm_self_introduction),
    }
    # 金鑰沒設時 provider 不存在 → chat 時 KeyError → call_and_parse 軟失敗，服務仍可啟動
    return LLMClient(providers=providers, routing=routing)


@lru_cache(maxsize=1)
def get_image_provider():
    """圖片生成 provider：預設 Vertex（gemini-3.1-flash-image，ADC 認證）；
    IMAGE_PROVIDER=cloudflare 切回備援。認證失敗時產圖拋錯 → service partial-fail 吸收。"""
    if settings.image_provider == "cloudflare":
        from app.services.image.client import CloudflareImageProvider

        return CloudflareImageProvider(
            account_id=settings.cloudflare_account_id or "",
            api_token=settings.cloudflare_api_token or "",
        )
    from app.services.image.client import GeminiImageProvider

    return GeminiImageProvider(settings.vertex_project_id, settings.vertex_location)


@lru_cache(maxsize=1)
def get_object_store():
    from app.storage.objects import GcsObjectStore

    return GcsObjectStore()


@lru_cache(maxsize=1)
def get_gallery():
    """gallery（quiz-gallery / share-image 寫入）：有 DATABASE_URL 走 Neon、否則記憶體。"""
    if settings.database_url:
        from app.storage.pg import PostgresGallery

        return PostgresGallery(settings.database_url)
    from app.storage.gallery import InMemoryGallery

    return InMemoryGallery()


@lru_cache(maxsize=1)
def get_kv():
    """KV 快取單例（quiz-completions 24h 快取、階段 8 產圖鎖）。

    有 DATABASE_URL 走 Neon Postgres（跨請求/跨重啟持久）；
    沒設時退回記憶體版（本地開發、測試用）。
    """
    if settings.database_url:
        from app.storage.pg import PostgresKV

        return PostgresKV(settings.database_url)
    return InMemoryKV()
