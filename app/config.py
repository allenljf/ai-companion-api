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

    data_dir: Path = BASE_DIR / "data"


settings = Settings()
