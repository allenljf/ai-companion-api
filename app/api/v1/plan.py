"""Phase 2 路由（/v1/plan/*）。"""

from fastapi import APIRouter, Depends

from app.api.deps import get_llm_client
from app.schemas.common import success_envelope
from app.schemas.plan import (
    TravelSummaryFromHistoryRequest,
    TravelSummaryFromOrdersRequest,
    TravelSummaryFromWishRequest,
    TravelSummaryRequest,
)
from app.services.llm.client import LLMClient
from app.services.plan.summary import summarize
from app.services.plan.summary_from_history import summarize_from_history
from app.services.plan.summary_from_orders import summarize_from_orders
from app.services.plan.summary_from_wish import summarize_from_wish

router = APIRouter(prefix="/v1/plan", tags=["plan"])


@router.post("/travel-summary")
async def travel_summary(
    body: TravelSummaryRequest, client: LLMClient = Depends(get_llm_client)
) -> dict:
    # 軟失敗契約：LLM 失敗也回 200 + fail_reason（source-spec 技法 3）
    data = await summarize(body.model_dump(), client)
    return success_envelope(data)


@router.post("/travel-summary-from-orders")
async def travel_summary_from_orders(
    body: TravelSummaryFromOrdersRequest, client: LLMClient = Depends(get_llm_client)
) -> dict:
    data = await summarize_from_orders(body.model_dump(), client)
    return success_envelope(data)


@router.post("/travel-summary-from-wish")
async def travel_summary_from_wish(
    body: TravelSummaryFromWishRequest, client: LLMClient = Depends(get_llm_client)
) -> dict:
    data = await summarize_from_wish(body.model_dump(), client)
    return success_envelope(data)


@router.post("/travel-summary-from-history")
async def travel_summary_from_history(
    body: TravelSummaryFromHistoryRequest, client: LLMClient = Depends(get_llm_client)
) -> dict:
    data = await summarize_from_history(body.model_dump(), client)
    return success_envelope(data)
