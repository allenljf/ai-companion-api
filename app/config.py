from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "dev"

    # 階段 1 起使用；階段 0 只要能為空啟動
    gemini_api_key: str | None = None
    groq_api_key: str | None = None

    # Neon Postgres（階段 6 前主要當 cache/gallery/lock 用）
    database_url: str | None = None

    # LLM 任務路由："provider:model[:reasoning_effort]"（provider = gemini | groq）
    # 每支 API 可獨立指定模型（CLAUDE.md 技術決策），換模型改 env 即可。
    # Qwen 是 reasoning 模型，輕量任務關掉思考（:none）避免 tokens 被吃光
    # 2026-08-16 全面切 Vertex AI（原 Groq 免費層：qwen TPM 8000 / llama TPM 12000 撞 429、
    # 簡繁混雜、且 Groq 無產圖生態；AI Studio 金鑰 prepay 額度耗盡 → 走 Vertex 吃 GCP 試用額度）。
    # gemini-3.6-flash 是 thinking 模型：輕量任務 :minimal 壓思考預算（對應 Groq 時代的 :none），
    # 重度結構化（guide/revise/completions）:low。要暫時切回 Groq 用 LLM_* env 覆寫即可。
    llm_travel_summary: str = "vertex:gemini-3.6-flash:minimal"
    llm_travel_summary_from_orders: str = "vertex:gemini-3.6-flash:minimal"
    llm_travel_summary_from_wish: str = "vertex:gemini-3.6-flash:minimal"
    llm_travel_summary_from_history: str = "vertex:gemini-3.6-flash:minimal"
    llm_recommend_city: str = "vertex:gemini-3.6-flash:minimal"
    llm_travel_guide: str = "vertex:gemini-3.6-flash:low"
    llm_travel_revise: str = "vertex:gemini-3.6-flash:low"
    llm_quiz: str = "vertex:gemini-3.6-flash:minimal"
    llm_quiz_completions: str = "vertex:gemini-3.6-flash:low"
    llm_self_introduction: str = "vertex:gemini-3.6-flash:minimal"

    # Vertex AI（LLM 主 provider）：Cloud Run 上以 runtime SA 的 ADC 認證，不需金鑰
    vertex_project_id: str = "ai-companion-505507"
    vertex_location: str = "global"

    # 圖片生成（階段 8）：Cloudflare Workers AI，免費層 10,000 neurons/天
    cloudflare_account_id: str | None = None
    cloudflare_api_token: str | None = None

    data_dir: Path = BASE_DIR / "data"


settings = Settings()
