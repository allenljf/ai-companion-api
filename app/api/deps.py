"""依賴注入：LLM client 組裝（provider 抽象 + 每支 API 可獨立指定模型）。"""

from functools import lru_cache

from app.config import settings
from app.services.llm.client import LLMClient, OpenAICompatProvider
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
    providers: dict[str, OpenAICompatProvider] = {}
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
def get_kv() -> InMemoryKV:
    """KV 快取單例（quiz-completions 24h 分析快取）。

    階段 6 前為記憶體版：Cloud Run 重啟即遺失、多副本不共用（限制記於 migration-plan）。
    """
    return InMemoryKV()
