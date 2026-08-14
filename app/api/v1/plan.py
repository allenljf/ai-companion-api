"""Phase 2 路由（/v1/plan/*）。"""

from fastapi import APIRouter, Depends

from app.api.deps import get_llm_client
from app.schemas.common import success_envelope
from app.schemas.plan import TravelSummaryRequest
from app.services.llm.client import LLMClient
from app.services.plan.summary import summarize

router = APIRouter(prefix="/v1/plan", tags=["plan"])


@router.post("/travel-summary")
async def travel_summary(
    body: TravelSummaryRequest, client: LLMClient = Depends(get_llm_client)
) -> dict:
    # 軟失敗契約：LLM 失敗也回 200 + fail_reason（source-spec 技法 3）
    data = await summarize(body.model_dump(), client)
    return success_envelope(data)
