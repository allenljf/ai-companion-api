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
    llm_travel_summary_from_orders: str = "groq:qwen/qwen3.6-27b:none"
    llm_travel_summary_from_wish: str = "groq:qwen/qwen3.6-27b:none"
    llm_travel_summary_from_history: str = "groq:qwen/qwen3.6-27b:none"
    llm_recommend_city: str = "groq:qwen/qwen3.6-27b:none"
    # guide 例外走 llama-3.3-70b：qwen 的 TPM 8000 裝不下「長 prompt + max_tokens 8000」（實測 413），
    # llama-3.3-70b TPM 12000 才夠；非 reasoning 模型，不需要 effort 參數
    llm_travel_guide: str = "groq:llama-3.3-70b-versatile"
    # revise 輸入含完整行程 + 完整對話，比 guide 更長，同樣只有 llama 的 TPM 12000 裝得下
    llm_travel_revise: str = "groq:llama-3.3-70b-versatile"
    # Phase 1：quiz 改寫 / 自我介紹是輕量任務走 qwen；completions 是重度結構化（15 keys +
    # reasoning 10 句 + 多約束）走 llama-3.3-70b（同 guide 的取捨，繁中靠 OpenCC 後處理）
    llm_quiz: str = "groq:qwen/qwen3.6-27b:none"
    llm_quiz_completions: str = "groq:llama-3.3-70b-versatile"
    llm_self_introduction: str = "groq:qwen/qwen3.6-27b:none"

    # 圖片生成（階段 8）：Cloudflare Workers AI，免費層 10,000 neurons/天
    cloudflare_account_id: str | None = None
    cloudflare_api_token: str | None = None

    data_dir: Path = BASE_DIR / "data"


settings = Settings()
