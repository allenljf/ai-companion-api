"""travel-guide 的 prompt 回歸測試（真實 LLM，手動執行）。

執行：pytest -m prompt_regression tests/prompt_regression/test_guide.py
重點驗證 migration-plan 6.4：天數符合 duration、city 一致、orders/products 全部排入、
無捏造價格、name/text 分工正確。

注意 Groq 免費層 TPM 8000：guide prompt + max_tokens 很吃額度，測項之間要間隔跑。
"""

import json
import re

import pytest

from app.api.deps import get_llm_client
from app.services.plan.guide import generate

pytestmark = pytest.mark.prompt_regression

# 商業資訊禁令（規則 8）：價格/營業時間樣式
PRICE_RE = re.compile(r"(NT\$|TWD|日圓|円|\d+\s*元|營業時間|营业时间)")


@pytest.fixture(autouse=True)
def fresh_llm_client():
    get_llm_client.cache_clear()
    yield
    get_llm_client.cache_clear()


@pytest.mark.asyncio
async def test_guide_respects_city_and_day_count():
    result = await generate(
        {
            "summary": "想去京都放鬆三天，喜歡古蹟與庭園",
            "city": "京都",
            "preferences": {"duration": "3天", "pace": "輕鬆"},
        },
        get_llm_client(),
    )
    assert result["fail_reason"] is None, result["fail_reason"]
    assert result["city"] == "京都"
    days = result["itinerary_patch"]["days"]
    assert len(days) == 3
    assert [d["day"] for d in days] == [1, 2, 3]
    # 排出來的天都要有具體 items（誠實優於填充：排不出要進 unplanned）
    for day in days:
        if day["status"] != "unplanned":
            assert day["items"], f"Day {day['day']} 沒有 items 卻不是 unplanned"


@pytest.mark.asyncio
async def test_guide_never_fabricates_prices():
    result = await generate(
        {
            "summary": "想去東京血拚跟吃美食",
            "city": "東京",
            "preferences": {"duration": "2天", "theme": "美食購物"},
        },
        get_llm_client(),
    )
    assert result["fail_reason"] is None, result["fail_reason"]
    text = json.dumps(result["itinerary_patch"], ensure_ascii=False)
    assert not PRICE_RE.search(text), f"疑似捏造商業資訊：{PRICE_RE.search(text).group(0)}"


@pytest.mark.asyncio
async def test_guide_schedules_all_required_orders_and_products():
    result = await generate(
        {
            "summary": "去慕尼黑五天，已訂了新天鵝堡行程",
            "city": "慕尼黑",
            "preferences": {"duration": "5天"},
            "orders": [
                {"oid": "26KK216164788", "prod_name": "新天鵝堡一日遊", "go_dt": "2026-09-02"}
            ],
            "products": [
                {"prod_id": "157138", "prod_name": "慕尼黑啤酒節導覽", "destination_names": ["慕尼黑"]}
            ],
        },
        get_llm_client(),
    )
    assert result["fail_reason"] is None, result["fail_reason"]
    days = result["itinerary_patch"]["days"]
    all_oids = {i.get("oid") for d in days for i in d["items"]}
    all_prod_ids = {i.get("prod_id") for d in days for i in d["items"]}
    assert "26KK216164788" in all_oids, "已預訂訂單沒被排入行程"
    assert "157138" in all_prod_ids, "挑選商品沒被排入行程"
    # booked_anchor 只由 oid 推導
    anchored = [d for d in days if d["booked_anchor"]]
    assert len(anchored) == 1
    assert anchored[0]["booked_anchor"] == {"oids": ["26KK216164788"]}


@pytest.mark.asyncio
async def test_guide_name_text_division_and_time_required():
    result = await generate(
        {
            "summary": "大阪兩天快閃",
            "city": "大阪",
            "preferences": {"duration": "2天", "pace": "緊湊"},
        },
        get_llm_client(),
    )
    assert result["fail_reason"] is None, result["fail_reason"]
    items = [i for d in result["itinerary_patch"]["days"] for i in d["items"]]
    assert items
    spots_meals = [i for i in items if i["type"] in ("spot", "meal")]
    named = [i for i in spots_meals if i["name"]]
    # spot/meal 的 name 必填（容忍少量失守，過半即視為規則有效）
    assert len(named) >= len(spots_meals) * 0.8, f"name 缺漏過多：{spots_meals}"
    timed = [i for i in items if i["time"]]
    assert len(timed) >= len(items) * 0.8, "time 必填規則失守過多"
