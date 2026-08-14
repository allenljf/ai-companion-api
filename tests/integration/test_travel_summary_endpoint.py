"""POST /v1/plan/travel-summary 端到端（mock LLM）：四入口 + 400 驗證契約（階段 1 驗收）。"""

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_llm_client
from app.main import app
from app.services.llm.client import LLMClient, LLMProvider


class StubProvider(LLMProvider):
    def __init__(self):
        self.reply = '{"summary": "開場白", "city": "京都"}'
        self.calls = 0

    async def chat(self, system, user, *, max_tokens, model, content_parts=None, json_mode=False, reasoning_effort=None):
        self.calls += 1
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


@pytest.fixture
def stub() -> StubProvider:
    provider = StubProvider()
    app.dependency_overrides[get_llm_client] = lambda: LLMClient(
        providers={"stub": provider}, routing={"travel_summary": ("stub", "stub-model")}
    )
    yield provider
    app.dependency_overrides.clear()


@pytest.fixture
def client(stub) -> TestClient:
    return TestClient(app)


URL = "/v1/plan/travel-summary"


class TestValidation400:
    def test_missing_entry_type(self, client):
        res = client.post(URL, json={})
        assert res.status_code == 400
        body = res.json()
        assert body["metadata"]["status"] == "110001"
        assert isinstance(body["metadata"]["desc"], list)
        assert "data" not in body  # 400 信封沒有 data 欄位（source-spec 2.3）

    def test_invalid_entry_type(self, client):
        assert client.post(URL, json={"entry_type": "nope"}).status_code == 400

    def test_quiz_completion_requires_city(self, client):
        assert client.post(URL, json={"entry_type": "quiz_completion"}).status_code == 400

    def test_from_orders_requires_city(self, client):
        assert client.post(URL, json={"entry_type": "from_orders"}).status_code == 400

    def test_imported_requires_source_type(self, client):
        assert client.post(URL, json={"entry_type": "imported_itinerary"}).status_code == 400

    def test_source_type_text_requires_content(self, client):
        res = client.post(URL, json={"entry_type": "imported_itinerary", "source_type": "text"})
        assert res.status_code == 400

    def test_source_type_image_requires_image_urls(self, client):
        res = client.post(URL, json={"entry_type": "imported_itinerary", "source_type": "image"})
        assert res.status_code == 400

    def test_image_urls_max_6(self, client):
        res = client.post(
            URL,
            json={
                "entry_type": "imported_itinerary",
                "source_type": "image",
                "image_urls": [f"https://x/{i}.png" for i in range(7)],
            },
        )
        assert res.status_code == 400

    def test_city_max_length_50(self, client):
        res = client.post(URL, json={"entry_type": "quiz_completion", "city": "字" * 51})
        assert res.status_code == 400


class TestFourEntryTypes:
    def test_from_zero_no_llm(self, client, stub):
        res = client.post(URL, json={"entry_type": "from_zero", "companion_name": "阿旅"})
        assert res.status_code == 200
        body = res.json()
        assert body["metadata"] == {"status": "0000", "desc": "Success"}
        data = body["data"]
        assert data["summary"].startswith("嗨，我是阿旅！")
        assert data["city"] == ""
        assert data["ai_model"] is None
        assert data["fail_reason"] is None
        assert stub.calls == 0

    def test_quiz_completion_authority_city(self, client, stub):
        res = client.post(
            URL,
            json={"entry_type": "quiz_completion", "city": "大阪", "intro_text": "你是私房鑑賞家"},
        )
        data = res.json()["data"]
        assert data["city"] == "大阪"  # LLM 回「京都」也不採信
        assert data["summary"] == "開場白"
        assert data["ai_model"] == "stub-model"
        assert stub.calls == 1

    def test_from_orders_authority_city(self, client, stub):
        res = client.post(
            URL,
            json={
                "entry_type": "from_orders",
                "city": "東京",
                "order": {"prod_name": "門票", "go_dt": "2026-09-02"},
            },
        )
        assert res.json()["data"]["city"] == "東京"

    def test_imported_itinerary_city_from_llm(self, client, stub):
        res = client.post(
            URL,
            json={"entry_type": "imported_itinerary", "source_type": "text", "content": "Day 1"},
        )
        assert res.json()["data"]["city"] == "京都"

    def test_llm_failure_returns_200_soft_fail(self, client, stub):
        stub.reply = RuntimeError("provider down")
        res = client.post(URL, json={"entry_type": "quiz_completion", "city": "大阪"})
        assert res.status_code == 200  # 軟失敗絕不回 5xx
        data = res.json()["data"]
        assert data["fail_reason"] is not None
        assert data["city"] == "大阪"
        assert data["summary"]  # 有可渲染的兜底內容
