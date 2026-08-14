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
    llm_travel_summary: str = "groq:qwen/qwen3.6-27b:none"

    data_dir: Path = BASE_DIR / "data"


settings = Settings()
