"""Phase 2 路由（/v1/plan/*）。"""

from fastapi import APIRouter, Depends

from app.core.throttle import throttle

from app.api.deps import get_llm_client
from app.schemas.common import success_envelope
from app.schemas.plan import (
    RecommendCityRequest,
    TravelGuideRequest,
    TravelReviseRequest,
    TravelSummaryFromHistoryRequest,
    TravelSummaryFromOrdersRequest,
    TravelSummaryFromWishRequest,
    TravelSummaryRequest,
)
from app.services.llm.client import LLMClient
from app.services.plan.guide import generate as generate_guide
from app.services.plan.recommend_city import recommend
from app.services.plan.revise import revise as revise_itinerary
from app.services.plan.summary import summarize
from app.services.plan.summary_from_history import summarize_from_history
from app.services.plan.summary_from_orders import summarize_from_orders
from app.services.plan.summary_from_wish import summarize_from_wish

router = APIRouter(prefix="/v1/plan", tags=["plan"])


@router.post("/travel-summary", summary="聊天室初始化摘要（四入口）",
             dependencies=[Depends(throttle("travel_summary"))])
async def travel_summary(
    body: TravelSummaryRequest, client: LLMClient = Depends(get_llm_client)
) -> dict:
    # 軟失敗契約：LLM 失敗也回 200 + fail_reason（source-spec 技法 3）
    data = await summarize(body.model_dump(), client)
    return success_envelope(data)


@router.post("/travel-summary-from-orders", summary="帶訂單開場：判讀訂單城市",
             dependencies=[Depends(throttle("from_orders"))])
async def travel_summary_from_orders(
    body: TravelSummaryFromOrdersRequest, client: LLMClient = Depends(get_llm_client)
) -> dict:
    data = await summarize_from_orders(body.model_dump(), client)
    return success_envelope(data)


@router.post("/travel-summary-from-wish", summary="從願望清單開場：商品→城市判讀",
             dependencies=[Depends(throttle("from_wish"))])
async def travel_summary_from_wish(
    body: TravelSummaryFromWishRequest, client: LLMClient = Depends(get_llm_client)
) -> dict:
    data = await summarize_from_wish(body.model_dump(), client)
    return success_envelope(data)


@router.post("/recommend-city", summary="城市推薦多輪對話（第 5 輪強制收斂）",
             dependencies=[Depends(throttle("recommend_city"))])
async def recommend_city(
    body: RecommendCityRequest, client: LLMClient = Depends(get_llm_client)
) -> dict:
    data = await recommend(body.model_dump(), client)
    return success_envelope(data)


@router.post("/travel-guide", summary="一次性產出完整逐日行程",
             dependencies=[Depends(throttle("travel_guide"))])
async def travel_guide(
    body: TravelGuideRequest, client: LLMClient = Depends(get_llm_client)
) -> dict:
    data = await generate_guide(body.model_dump(), client)
    return success_envelope(data)


@router.post("/travel-revise", summary="自然語言修改行程（回應恆為完整行程）",
             dependencies=[Depends(throttle("travel_revise"))])
async def travel_revise(
    body: TravelReviseRequest, client: LLMClient = Depends(get_llm_client)
) -> dict:
    data = await revise_itinerary(body.model_dump(), client)
    return success_envelope(data)


@router.post("/travel-summary-from-history", summary="從瀏覽紀錄開場：商品→城市判讀",
             dependencies=[Depends(throttle("from_history"))])
async def travel_summary_from_history(
    body: TravelSummaryFromHistoryRequest, client: LLMClient = Depends(get_llm_client)
) -> dict:
    data = await summarize_from_history(body.model_dump(), client)
    return success_envelope(data)
