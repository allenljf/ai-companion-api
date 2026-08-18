"""POST /v1/plan/travel-guide 端到端（mock LLM）：400 契約 + 軟失敗 + 成功包裝（階段 4 驗收）。"""

import json

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_llm_client
from app.main import app
from app.services.llm.client import LLMClient, LLMProvider


class StubProvider(LLMProvider):
    def __init__(self):
        self.reply = "{}"

    async def chat(self, system, user, *, max_tokens, model, content_parts=None, json_mode=False, reasoning_effort=None):
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


@pytest.fixture
def stub() -> StubProvider:
    provider = StubProvider()
    app.dependency_overrides[get_llm_client] = lambda: LLMClient(
        providers={"stub": provider}, routing={"travel_guide": ("stub", "stub-model")}
    )
    yield provider
    app.dependency_overrides.clear()


@pytest.fixture
def client(stub) -> TestClient:
    return TestClient(app)


URL = "/v1/plan/travel-guide"

VALID_BODY = {
    "summary": "想去京都放鬆三天",
    "city": "京都",
    "preferences": {"duration": "3天", "pace": "輕鬆"},
}

LLM_OK = json.dumps(
    {
        "city": "京都",
        "days": 1,
        "date_range": None,
        "messages": [{"type": "completion", "text": "行程排好了！"}],
        "unplanned_days": [],
        "pending_fields": [],
        "itinerary": [
            {
                "day": 1,
                "status": "planned",
                "kind": "normal",
                "half_day": False,
                "items": [
                    {"name": "清水寺", "text": "清晨人少", "type": "spot",
                     "time": "09:00", "time_band": "上午", "lat": 34.99, "lng": 135.78}
                ],
            }
        ],
    },
    ensure_ascii=False,
)


class TestValidation400:
    def test_missing_summary(self, client):
        res = client.post(URL, json={"city": "京都"})
        assert res.status_code == 400
        assert res.json()["metadata"]["status"] == "110001"

    def test_summary_too_long(self, client):
        assert client.post(URL, json={"summary": "x" * 4001}).status_code == 400

    def test_city_too_long(self, client):
        assert client.post(URL, json=VALID_BODY | {"city": "x" * 51}).status_code == 400

    def test_preferences_too_many_keys(self, client):
        prefs = {f"k{i}": "v" for i in range(31)}
        assert client.post(URL, json=VALID_BODY | {"preferences": prefs}).status_code == 400



class TestSoftFailure200:
    def test_llm_exception_returns_200_with_fail_reason(self, client, stub):
        stub.reply = RuntimeError("boom")
        res = client.post(URL, json=VALID_BODY)
        assert res.status_code == 200
        data = res.json()["data"]
        assert data["fail_reason"] is not None
        assert data["itinerary_patch"] == {"mode": "full", "changed_days": [], "days": []}
        assert data["messages"][0]["text"] == "嗯…行程排到一半卡住了，要不要再試一次？"

    def test_city_mismatch_returns_200_with_fail_reason(self, client, stub):
        stub.reply = LLM_OK.replace("京都", "大阪")
        res = client.post(URL, json=VALID_BODY)
        assert res.status_code == 200
        assert res.json()["data"]["fail_reason"] is not None


class TestSuccess:
    def test_city_variant_returns_exact_requested_city(self, client, stub):
        stub.reply = LLM_OK.replace("京都", "臺北")
        res = client.post(URL, json=VALID_BODY | {"city": "台北"})
        data = res.json()["data"]
        assert res.status_code == 200
        assert data["fail_reason"] is None
        assert data["city"] == "台北"

    def test_full_envelope(self, client, stub):
        stub.reply = LLM_OK
        res = client.post(URL, json=VALID_BODY)
        assert res.status_code == 200
        body = res.json()
        assert body["metadata"]["status"] == "0000"
        data = body["data"]
        assert data["fail_reason"] is None
        assert data["city"] == "京都"
        assert data["phase"] == "done"
        assert data["itinerary_patch"]["mode"] == "full"
        [day] = data["itinerary_patch"]["days"]
        assert day["day"] == 1 and day["booked_anchor"] is None
        [item] = day["items"]
        assert item["type"] == "spot" and "lat" in item and "transport_mode" not in item

    def test_extra_orders_products_fields_ignored(self, client, stub):
        # 2026-08-16 起本 API 不收 orders/products；多帶不報錯（Pydantic 預設忽略未知欄位）
        stub.reply = LLM_OK
        res = client.post(
            URL,
            json=VALID_BODY | {
                "orders": [{"oid": "26KK1", "prod_name": "門票"}],
                "products": [{"prod_id": "P1", "prod_name": "商品"}],
            },
        )
        assert res.status_code == 200
        day = res.json()["data"]["itinerary_patch"]["days"][0]
        assert day["items"][0]["oid"] is None  # 不會產生任何 id 對映
        assert day["booked_anchor"] is None
