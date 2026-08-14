"""三支城市判讀的 prompt 回歸測試（真實 LLM，手動執行）。

執行：pytest -m prompt_regression tests/prompt_regression/test_city_reading.py
原則：測「性質」不測「精確文字」（migration-plan 6.3 / 6.4）。
"""

import pytest

from app.api.deps import get_llm_client
from app.services.plan.summary_from_history import summarize_from_history
from app.services.plan.summary_from_orders import summarize_from_orders
from app.services.plan.summary_from_wish import summarize_from_wish

pytestmark = pytest.mark.prompt_regression


@pytest.fixture(autouse=True)
def fresh_llm_client():
    get_llm_client.cache_clear()
    yield
    get_llm_client.cache_clear()


@pytest.mark.asyncio
async def test_from_orders_position_mapping_and_city_specificity():
    orders = [
        {"prod_name": "東京晴空塔展望台門票", "package_name": "當日券・成人", "destination_name": "東京"},
        {"prod_name": "環球影城門票", "package_name": "1 日券", "destination_name": "美國"},
        {"prod_name": "小樽運河遊船", "package_name": "白天航班", "destination_name": "北海道"},
    ]
    result = await summarize_from_orders({"orders": orders}, get_llm_client())

    assert result["fail_reason"] is None
    # cities 長度 == orders 長度，order_index 依位置（migration-plan 6.4）
    assert [o["order_index"] for o in result["options"]] == [0, 1, 2]
    assert result["options"][0]["city"] == "東京"
    # 第 3 筆「北海道」是行政區域，必須收斂到具體城市（不可原樣回「北海道」）
    assert result["options"][2]["city"] not in ("北海道", "日本", "")
    # greeting 不可條列城市名稱
    assert "東京" not in result["greeting"]


@pytest.mark.asyncio
async def test_from_wish_aggregates_and_maps_products():
    products = [
        {"prod_id": "P100", "prod_name": "從慕尼黑出發的新天鵝堡冬季之旅",
         "introduction": "參觀由路德維希二世建造的新天鵝堡", "destination_names": ["新天鵝堡"]},
        {"prod_id": "P200", "prod_name": "西班牙馬德里一日遊",
         "introduction": "含普拉多博物館快速通關門票", "destination_names": ["普拉多博物館"]},
        {"prod_id": "P300", "prod_name": "慕尼黑啤酒節入場體驗",
         "introduction": "暢飲德國經典啤酒", "destination_names": ["慕尼黑"]},
    ]
    result = await summarize_from_wish({"products": products}, get_llm_client())

    assert result["fail_reason"] is None
    assert 1 <= len(result["cities"]) <= 3
    # 所有解析出的 prod_id 必須來自輸入（不可能捏造——結構保證，這裡驗證整條鏈路）
    all_ids = {p["prod_id"] for c in result["cities"] for p in c["products"]}
    assert all_ids <= {"P100", "P200", "P300"}
    # 慕尼黑應把景點商品（新天鵝堡）與同城商品合併
    munich = next((c for c in result["cities"] if "慕尼黑" in c["city"]), None)
    assert munich is not None
    assert {p["prod_id"] for p in munich["products"]} == {"P100", "P300"}


@pytest.mark.asyncio
async def test_from_history_landmark_to_city_convergence():
    products = [
        {"prod_id": "H1", "prod_name": "大阪環球影城門票",
         "introduction": "一日券暢玩全園", "destination_names": ["大阪"]},
        {"prod_id": "H2", "prod_name": "大阪周遊卡二日券",
         "introduction": "地鐵巴士無限搭乘", "destination_names": ["大阪"]},
    ]
    result = await summarize_from_history({"products": products}, get_llm_client())

    assert result["fail_reason"] is None
    # 兩筆同城必須合併成一個城市（去重）
    assert len(result["cities"]) == 1
    assert result["cities"][0]["city"] == "大阪"
    assert {p["prod_id"] for p in result["cities"][0]["products"]} == {"H1", "H2"}
