"""POST /v1/plan/recommend-city 端到端（mock LLM）：400 契約 + 輪次/換城行為（階段 3 驗收）。"""

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
        providers={"stub": provider}, routing={"recommend_city": ("stub", "stub-model")}
    )
    yield provider
    app.dependency_overrides.clear()


@pytest.fixture
def client(stub) -> TestClient:
    return TestClient(app)


URL = "/v1/plan/recommend-city"


class TestValidation400:
    def test_missing_messages(self, client):
        res = client.post(URL, json={})
        assert res.status_code == 400
        assert res.json()["metadata"]["status"] == "110001"

    def test_empty_messages(self, client):
        assert client.post(URL, json={"messages": []}).status_code == 400

    def test_more_than_20_messages(self, client):
        messages = [{"role": "user", "content": f"m{i}"} for i in range(21)]
        assert client.post(URL, json={"messages": messages}).status_code == 400

    def test_invalid_role(self, client):
        res = client.post(URL, json={"messages": [{"role": "system", "content": "x"}]})
        assert res.status_code == 400

    def test_missing_content(self, client):
        assert client.post(URL, json={"messages": [{"role": "user"}]}).status_code == 400

    def test_more_than_30_shown_cities(self, client):
        res = client.post(
            URL,
            json={
                "messages": [{"role": "user", "content": "hi"}],
                "shown_cities": [f"c{i}" for i in range(31)],
            },
        )
        assert res.status_code == 400


class TestBehavior:
    def test_not_converged_round_fields(self, client, stub):
        stub.reply = '{"reply": "想放鬆還是探險？", "quick_replies": ["放鬆", "探險"], "recommended_city": "", "city_reason": "", "off_topic": false}'
        res = client.post(URL, json={"messages": [{"role": "user", "content": "想出去玩"}]})
        assert res.status_code == 200
        data = res.json()["data"]
        assert data["round"] == 1
        assert data["remaining_rounds"] == 4
        assert data["is_final"] is False

    def test_converged_fixed_chips(self, client, stub):
        stub.reply = '{"reply": "推薦沖繩！", "quick_replies": ["ok"], "recommended_city": "沖繩", "city_reason": "海", "off_topic": false}'
        res = client.post(URL, json={"messages": [{"role": "user", "content": "想看海"}]})
        data = res.json()["data"]
        assert data["is_final"] is True
        assert data["quick_replies"] == ["就去沖繩！", "換一個城市", "重新聊聊"]

    def test_swap_limit_exceeded_returns_200_without_llm(self, client, stub):
        stub.reply = RuntimeError("must not be called")
        res = client.post(
            URL,
            json={
                "messages": [{"role": "user", "content": "換"}],
                "shown_cities": [f"城市{i}" for i in range(6)],
            },
        )
        assert res.status_code == 200
        data = res.json()["data"]
        assert data["swap_limit_reached"] is True
        assert data["ai_model"] is None
        assert data["fail_reason"] is None

    def test_llm_failure_soft_fails_200(self, client, stub):
        stub.reply = RuntimeError("down")
        res = client.post(URL, json={"messages": [{"role": "user", "content": "hi"}]})
        assert res.status_code == 200
        data = res.json()["data"]
        assert data["fail_reason"] is not None
        assert data["reply"]  # 有可渲染的兜底內容
