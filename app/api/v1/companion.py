import asyncio
import json
import logging
from functools import lru_cache

from fastapi import APIRouter, Depends

from app.api.deps import (
    get_gallery,
    get_image_provider,
    get_kv,
    get_llm_client,
    get_object_store,
)
from app.config import settings
from app.core.throttle import throttle
from app.schemas.common import business_error_envelope, success_envelope
from app.schemas.companion import (
    QuizCompletionsRequest,
    QuizFetchRequest,
    SelfIntroductionRequest,
    ShareImageV2Request,
)
from app.services.companion.completion import complete_quiz
from app.services.companion.quiz import fetch_quiz
from app.services.companion.self_introduction import generate_self_introduction
from app.services.companion.share_image import QuizSessionNotFound, ShareImageV2Service
from app.services.llm.client import LLMClient

router = APIRouter(prefix="/v1/companion", tags=["companion"])
logger = logging.getLogger(__name__)

GALLERY_RETRY_DELAY_SECONDS = 0.25


@lru_cache(maxsize=1)
def _load_ai_partner() -> dict:
    path = settings.data_dir / "ai_partner.json"
    return json.loads(path.read_text(encoding="utf-8"))


@router.get("/ai-partner", summary="旅伴設定選項（人格/風格/性別/頭像）")
async def get_ai_partner() -> dict:
    # 原樣透傳 data/ai_partner.json（原始服務是透傳 DCS ai_partner variant）
    # 不走 LLM、無 throttle（docs/source-spec.md 4.1 / 2.4）
    return success_envelope(_load_ai_partner())


@router.post("/quiz", summary="測驗題目：加權隨機選題 + 旅伴語氣改寫")
async def quiz(body: QuizFetchRequest, client: LLMClient = Depends(get_llm_client)) -> dict:
    # 軟失敗契約：LLM 改寫失敗回原始題目 + fail_reason（source-spec 4.2）
    data = await fetch_quiz(body.model_dump(), client)
    return success_envelope(data)


@router.post("/quiz-completions", summary="提交答案：八人格判定 + 文案生成（快取 24h）")
async def quiz_completions(
    body: QuizCompletionsRequest,
    client: LLMClient = Depends(get_llm_client),
    kv=Depends(get_kv),
) -> dict:
    params = body.model_dump()
    params["completion_uuid"] = str(body.completion_uuid)
    data = await complete_quiz(params, client, kv)
    return success_envelope(data)


@router.post("/share-image-v2", summary="分享海報素材（hero + 郵戳 + 3 tag 插畫）",
             dependencies=[Depends(throttle("share_image_v2"))])
async def share_image_v2(
    body: ShareImageV2Request,
    provider=Depends(get_image_provider),
    kv=Depends(get_kv),
    gallery=Depends(get_gallery),
    store=Depends(get_object_store),
) -> dict:
    service = ShareImageV2Service(provider=provider, kv=kv, gallery=gallery, store=store)
    try:
        data = await service.generate(str(body.completion_uuid), body.partner_image_url)
    except QuizSessionNotFound:
        # 分析快取過期（>24h）或沒做過測驗：HTTP 200 + C007，引導重跑測驗（source-spec 6.5）
        return business_error_envelope("C007", "測驗分析結果不存在或已過期，請重新完成測驗")
    return success_envelope(data)


@router.get("/quiz-gallery", summary="其他人的測驗結果牆（只含產圖成功項）")
async def quiz_gallery(gallery=Depends(get_gallery)) -> dict:
    """其他人的測驗結果牆（source-spec 4.6）：只收錄產圖成功的項目，新到舊。"""
    try:
        records = await gallery.list()
    except Exception as exc:
        # Neon 閒置後的 lazy pool 首次連線偶爾會失敗；這是唯讀查詢，可安全重試一次。
        logger.warning("quiz gallery read failed; retrying once: %s", exc)
        await asyncio.sleep(GALLERY_RETRY_DELAY_SECONDS)
        records = await gallery.list()
    records = [
        record
        for record in records
        if isinstance(record, dict) and record.get("share_image_url")
    ]
    return success_envelope({"count": len(records), "items": records})


@router.post("/self-introduction", summary="旅伴自我介紹開場白",
             dependencies=[Depends(throttle("self_introduction"))])
async def self_introduction(
    body: SelfIntroductionRequest, client: LLMClient = Depends(get_llm_client)
) -> dict:
    data = await generate_self_introduction(body.model_dump(), client)
    return success_envelope(data)
