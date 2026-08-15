import json
from functools import lru_cache

from fastapi import APIRouter, Depends

from app.api.deps import get_kv, get_llm_client
from app.config import settings
from app.schemas.common import success_envelope
from app.schemas.companion import (
    QuizCompletionsRequest,
    QuizFetchRequest,
    SelfIntroductionRequest,
)
from app.services.companion.completion import complete_quiz
from app.services.companion.quiz import fetch_quiz
from app.services.companion.self_introduction import generate_self_introduction
from app.services.llm.client import LLMClient
from app.storage.kv import InMemoryKV

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


@router.post("/quiz")
async def quiz(body: QuizFetchRequest, client: LLMClient = Depends(get_llm_client)) -> dict:
    # 軟失敗契約：LLM 改寫失敗回原始題目 + fail_reason（source-spec 4.2）
    data = await fetch_quiz(body.model_dump(), client)
    return success_envelope(data)


@router.post("/quiz-completions")
async def quiz_completions(
    body: QuizCompletionsRequest,
    client: LLMClient = Depends(get_llm_client),
    kv: InMemoryKV = Depends(get_kv),
) -> dict:
    params = body.model_dump()
    params["completion_uuid"] = str(body.completion_uuid)
    data = await complete_quiz(params, client, kv)
    return success_envelope(data)


@router.post("/self-introduction")
async def self_introduction(
    body: SelfIntroductionRequest, client: LLMClient = Depends(get_llm_client)
) -> dict:
    data = await generate_self_introduction(body.model_dump(), client)
    return success_envelope(data)
