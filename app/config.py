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
    # 2026-08-18：Gemini 2.5 系列仍然可用，但最新的輕量主力已是 3.5 Flash-Lite；
    # 這裡改用 Vertex 的低成本新模型，維持文字路由穩定且更貼近最新官方列表。
    llm_travel_summary: str = "vertex:gemini-3.5-flash-lite"
    llm_recommend_city: str = "vertex:gemini-3.5-flash-lite"
    llm_travel_guide: str = "vertex:gemini-3.5-flash-lite"
    llm_travel_revise: str = "vertex:gemini-3.5-flash-lite"
    llm_quiz: str = "vertex:gemini-3.5-flash-lite"
    llm_quiz_completions: str = "vertex:gemini-3.5-flash-lite"
    llm_self_introduction: str = "vertex:gemini-3.5-flash-lite"

    # Vertex AI（LLM 主 provider）：Cloud Run 上以 runtime SA 的 ADC 認證，不需金鑰
    vertex_project_id: str = "ai-companion-505507"
    vertex_location: str = "global"

    # 圖片生成：優先改用 Cloudflare Workers AI 免費額度（10,000 neurons/天）
    # hero 用 flux-2-klein-4b（直式/參考圖模型）；裝飾素材用 flux-1-schnell（方圖）
    image_provider: str = "cloudflare"
    image_hero_model: str = "@cf/black-forest-labs/flux-2-klein-4b"
    image_decoration_model: str = "@cf/black-forest-labs/flux-1-schnell"

    # Cloudflare Workers AI（免費層 10,000 neurons/天，00:00 UTC 重置）
    cloudflare_account_id: str | None = None
    cloudflare_api_token: str | None = None

    data_dir: Path = BASE_DIR / "data"


settings = Settings()
