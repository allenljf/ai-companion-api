"""travel-revise 的 prompt 回歸測試（真實 LLM，手動執行）。

執行：pytest -m prompt_regression tests/prompt_regression/test_revise.py
重點驗證 migration-plan 6.4：未變動的天內容與輸入一致；離題時原樣返回；oid 項目沒被刪。

注意 Groq 免費層 TPM 12000（llama-3.3-70b）：revise 輸入含完整行程 + 對話，測項之間要間隔跑。
"""

import pytest

from app.api.deps import get_llm_client
from app.services.plan.revise import revise

pytestmark = pytest.mark.prompt_regression


@pytest.fixture(autouse=True)
def fresh_llm_client():
    get_llm_client.cache_clear()
    yield
    get_llm_client.cache_clear()


def kyoto_itinerary() -> list[dict]:
    return [
        {
            "day": 1, "status": "planned", "kind": "normal", "half_day": False,
            "items": [
                {"name": "清水寺", "text": "清晨人少、光線好", "type": "spot",
                 "time": "09:00", "time_band": "上午", "note": None, "lat": 34.9949, "lng": 135.785},
                {"name": None, "text": "步行前往下一站", "type": "logistics",
                 "time": "11:30", "time_band": "上午", "note": None, "transport_mode": "walk"},
                {"name": "一蘭拉麵", "text": "午餐時段", "type": "meal",
                 "time": "12:00", "time_band": "中午", "note": None},
            ],
        },
        {
            "day": 2, "status": "planned", "kind": "normal", "half_day": False,
            "items": [
                {"name": "嵐山竹林小道", "text": "靜謐的竹林步道", "type": "spot",
                 "time": "09:30", "time_band": "上午", "note": None, "lat": 35.0094, "lng": 135.6722,
                 "oid": "26KK216164788"},
            ],
        },
    ]


def base_params(request_text: str) -> dict:
    return {
        "itinerary": kyoto_itinerary(),
        "city": "京都",
        "messages": [
            {"role": "user", "content": "幫我排京都兩天"},
            {"role": "assistant", "content": "排好了！"},
            {"role": "user", "content": request_text},
        ],
    }


@pytest.mark.asyncio
async def test_revise_untouched_day_stays_identical():
    params = base_params("第一天中午改吃壽司，其他不要動")
    original_day2 = params["itinerary"][1]
    result = await revise(params, get_llm_client())
    assert result["fail_reason"] is None, result["fail_reason"]
    days = result["itinerary_patch"]["days"]
    assert len(days) == 2
    # Day 2 未變動：items 內容要與輸入一致（名稱/時間不得被順手改寫）
    day2_items = days[1]["items"]
    assert day2_items[0]["name"] == original_day2["items"][0]["name"]
    assert day2_items[0]["time"] == original_day2["items"][0]["time"]
    # Day 1 有變動且被 diff 抓到
    assert 1 in result["itinerary_patch"]["changed_days"]
    assert 2 not in result["itinerary_patch"]["changed_days"]


@pytest.mark.asyncio
async def test_revise_off_topic_returns_original():
    params = base_params("你可以教我寫 Python 嗎？")
    result = await revise(params, get_llm_client())
    assert result["fail_reason"] is None, result["fail_reason"]
    assert result["off_topic"] is True
    # 原樣返回：行程內容不變
    days = result["itinerary_patch"]["days"]
    assert days[0]["items"][0]["name"] == "清水寺"
    assert days[1]["items"][0]["name"] == "嵐山竹林小道"
    assert result["itinerary_patch"]["changed_days"] == []
    assert result["reply"]


@pytest.mark.asyncio
async def test_revise_refuses_to_delete_booked_oid_item():
    params = base_params("第二天的嵐山竹林不想去了，幫我刪掉")
    result = await revise(params, get_llm_client())
    assert result["fail_reason"] is None, result["fail_reason"]
    days = result["itinerary_patch"]["days"]
    # 已預訂項目不可刪：oid item 仍在行程中、anchor 仍在
    all_oids = {i.get("oid") for d in days for i in d["items"]}
    assert "26KK216164788" in all_oids, "已預訂項目被刪掉了"
    anchored = [d for d in days if d["booked_anchor"]]
    assert anchored and anchored[0]["booked_anchor"]["oids"] == ["26KK216164788"]


@pytest.mark.asyncio
async def test_revise_delete_day_renumbers():
    params = base_params("第一天整天取消，行程改成一天就好")
    result = await revise(params, get_llm_client())
    assert result["fail_reason"] is None, result["fail_reason"]
    days = result["itinerary_patch"]["days"]
    assert len(days) == 1
    assert days[0]["day"] == 1
    # 留下來的是原 Day 2（含已預訂項目）
    assert days[0]["items"][0]["oid"] == "26KK216164788"
