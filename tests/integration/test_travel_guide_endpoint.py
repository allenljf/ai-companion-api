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

    def test_orders_max_3(self, client):
        orders = [{"oid": f"O{i}", "prod_name": "門票"} for i in range(4)]
        assert client.post(URL, json=VALID_BODY | {"orders": orders}).status_code == 400

    def test_order_missing_oid(self, client):
        assert (
            client.post(URL, json=VALID_BODY | {"orders": [{"prod_name": "門票"}]}).status_code
            == 400
        )

    def test_order_bad_go_dt(self, client):
        orders = [{"oid": "O1", "prod_name": "門票", "go_dt": "2026/09/01"}]
        assert client.post(URL, json=VALID_BODY | {"orders": orders}).status_code == 400

    def test_products_max_10(self, client):
        products = [{"prod_id": f"P{i}", "prod_name": "商品"} for i in range(11)]
        assert client.post(URL, json=VALID_BODY | {"products": products}).status_code == 400

    def test_product_missing_prod_id(self, client):
        assert (
            client.post(URL, json=VALID_BODY | {"products": [{"prod_name": "商品"}]}).status_code
            == 400
        )


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

    def test_orders_flow_whitelist_and_anchor(self, client, stub):
        stub.reply = json.dumps(
            {
                "city": "大阪",
                "days": 1,
                "messages": [],
                "unplanned_days": [],
                "itinerary": [
                    {"day": 1, "items": [
                        {"name": "環球影城", "type": "spot", "oid": "26KK1", "lat": 1, "lng": 2},
                        {"name": "幻覺", "type": "spot", "oid": "FAKE"},
                    ]}
                ],
            },
            ensure_ascii=False,
        )
        res = client.post(
            URL,
            json={
                "summary": "大阪行",
                "city": "大阪",
                "orders": [{"oid": "26KK1", "prod_name": "環球影城門票", "go_dt": "2026-09-01"}],
            },
        )
        day = res.json()["data"]["itinerary_patch"]["days"][0]
        assert day["items"][0]["oid"] == "26KK1"
        assert day["items"][1]["oid"] is None
        assert day["booked_anchor"] == {"oids": ["26KK1"]}
