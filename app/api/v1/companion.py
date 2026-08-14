import json
from functools import lru_cache

from fastapi import APIRouter

from app.config import settings
from app.schemas.common import success_envelope

router = APIRouter(prefix="/v1/companion", tags=["companion"])


@lru_cache(maxsize=1)
def _load_ai_partner() -> dict:
    path = settings.data_dir / "ai_partner.json"
    return json.loads(path.read_text(encoding="utf-8"))


@router.get("/ai-partner")
async def get_ai_partner() -> dict:
    # 原樣透傳 data/ai_partner.json（原始服務是透傳 DCS ai_partner variant）
    # 不走 LLM、無 throttle（docs/source-spec.md 4.1 / 2.4）
    return success_envelope(_load_ai_partner())
