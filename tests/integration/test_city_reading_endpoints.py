"""三支城市判讀端點：400 驗證契約 + 端到端（mock LLM）（階段 2 驗收）。"""

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
    routing = {
        task: ("stub", "stub-model")
        for task in (
            "travel_summary_from_orders",
            "travel_summary_from_wish",
            "travel_summary_from_history",
        )
    }
    app.dependency_overrides[get_llm_client] = lambda: LLMClient(
        providers={"stub": provider}, routing=routing
    )
    yield provider
    app.dependency_overrides.clear()


@pytest.fixture
def client(stub) -> TestClient:
    return TestClient(app)


class TestFromOrdersEndpoint:
    URL = "/v1/plan/travel-summary-from-orders"

    def test_empty_orders_400(self, client):
        assert client.post(self.URL, json={"orders": []}).status_code == 400

    def test_more_than_3_orders_400(self, client):
        orders = [{"prod_name": f"商品{i}"} for i in range(4)]
        assert client.post(self.URL, json={"orders": orders}).status_code == 400

    def test_missing_prod_name_400(self, client):
        res = client.post(self.URL, json={"orders": [{"destination_name": "東京"}]})
        assert res.status_code == 400
        assert res.json()["metadata"]["status"] == "110001"

    def test_success_options_by_position(self, client, stub):
        stub.reply = '{"greeting": "有旅程囉", "cities": ["東京", ""]}'
        res = client.post(
            self.URL,
            json={
                "orders": [
                    {"prod_name": "晴空塔門票", "destination_name": "東京"},
                    {"prod_name": "神秘商品"},
                ]
            },
        )
        assert res.status_code == 200
        data = res.json()["data"]
        assert data["options"] == [
            {"order_index": 0, "city": "東京"},
            {"order_index": 1, "city": ""},
        ]
        assert data["fail_reason"] is None

    def test_count_mismatch_soft_fails_200(self, client, stub):
        stub.reply = '{"greeting": "hi", "cities": ["東京", "大阪", "京都"]}'
        res = client.post(self.URL, json={"orders": [{"prod_name": "晴空塔門票"}]})
        assert res.status_code == 200
        assert res.json()["data"]["fail_reason"] == "llm_error"


class TestFromWishEndpoint:
    URL = "/v1/plan/travel-summary-from-wish"

    def test_missing_prod_id_400(self, client):
        res = client.post(self.URL, json={"products": [{"prod_name": "門票"}]})
        assert res.status_code == 400

    def test_more_than_20_products_400(self, client):
        products = [{"prod_id": f"P{i}", "prod_name": f"商品{i}"} for i in range(21)]
        assert client.post(self.URL, json={"products": products}).status_code == 400

    def test_success_resolves_authoritative_prod_id(self, client, stub):
        stub.reply = '{"greeting": "看了你的收藏", "cities": [{"city": "慕尼黑", "product_indexes": [0, 99]}]}'
        res = client.post(
            self.URL,
            json={"products": [{"prod_id": "P100", "prod_name": "新天鵝堡之旅"}]},
        )
        data = res.json()["data"]
        # 超範圍 index 99 被忽略；prod_id 從請求原樣取回
        assert data["cities"] == [
            {"city": "慕尼黑", "products": [{"prod_id": "P100", "prod_name": "新天鵝堡之旅"}]}
        ]

    def test_llm_error_soft_fail_200(self, client, stub):
        stub.reply = RuntimeError("down")
        res = client.post(
            self.URL, json={"products": [{"prod_id": "P1", "prod_name": "商品"}]}
        )
        assert res.status_code == 200
        assert res.json()["data"]["fail_reason"] == "llm_error"


class TestFromHistoryEndpoint:
    URL = "/v1/plan/travel-summary-from-history"

    def test_validation_same_as_wish(self, client):
        assert client.post(self.URL, json={"products": []}).status_code == 400

    def test_success(self, client, stub):
        stub.reply = '{"greeting": "看了你瀏覽的", "cities": [{"city": "大阪", "product_indexes": [0]}]}'
        res = client.post(
            self.URL, json={"products": [{"prod_id": "H1", "prod_name": "環球影城門票"}]}
        )
        data = res.json()["data"]
        assert data["cities"][0]["products"][0]["prod_id"] == "H1"

    def test_low_confidence(self, client, stub):
        stub.reply = '{"greeting": "hi", "cities": []}'
        res = client.post(
            self.URL, json={"products": [{"prod_id": "H1", "prod_name": "商品"}]}
        )
        assert res.json()["data"]["fail_reason"] == "low_confidence"
