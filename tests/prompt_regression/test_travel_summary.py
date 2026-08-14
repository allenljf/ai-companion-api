"""travel-summary 的 prompt 回歸測試（真實 LLM，手動執行）。

執行：pytest -m prompt_regression tests/prompt_regression/test_travel_summary.py
原則：測「性質」不測「精確文字」（migration-plan 6.3）。
"""

import pytest

from app.api.deps import get_llm_client
from app.services.plan.summary import SOFT_FAIL_SUMMARY, summarize

pytestmark = pytest.mark.prompt_regression


@pytest.fixture(autouse=True)
def fresh_llm_client():
    # lru_cache 的 httpx.AsyncClient 會綁在第一個測試的 event loop 上，
    # pytest-asyncio 每個測試開新 loop → 這裡逐測試重建 client（正式環境單一 loop 無此問題）
    get_llm_client.cache_clear()
    yield
    get_llm_client.cache_clear()


@pytest.mark.asyncio
async def test_quiz_completion_authority_city_and_no_fail():
    result = await summarize(
        {
            "entry_type": "quiz_completion",
            "city": "大阪",
            "intro_text": "你是私房鑑賞家，喜歡在巷弄裡找隱藏美食。",
            "companion_name": "小K",
            "personality": "幽默",
            "speech_style": "親切",
        },
        get_llm_client(),
    )
    assert result["fail_reason"] is None
    assert result["city"] == "大阪"  # 權威輸入 100% 一致（migration-plan 6.4）
    assert result["summary"] and result["summary"] != SOFT_FAIL_SUMMARY
    assert "大阪" in result["summary"]  # 開場白要呼應城市


@pytest.mark.asyncio
async def test_from_orders_mentions_order_material():
    result = await summarize(
        {
            "entry_type": "from_orders",
            "city": "東京",
            "order": {"prod_name": "東京迪士尼樂園門票", "package_name": "一日護照", "go_dt": "2026-09-02"},
        },
        get_llm_client(),
    )
    assert result["fail_reason"] is None
    assert result["city"] == "東京"
    assert "迪士尼" in result["summary"]  # order 有內容時要自然帶到商品


@pytest.mark.asyncio
async def test_import_text_infers_city():
    result = await summarize(
        {
            "entry_type": "imported_itinerary",
            "source_type": "text",
            "content": "Day 1 清水寺、二年坂、祇園；Day 2 嵐山竹林、天龍寺；Day 3 伏見稻荷大社",
        },
        get_llm_client(),
    )
    assert result["fail_reason"] is None
    assert result["city"] == "京都"  # 這份行程的城市判讀應該非常明確
    assert result["summary"]
