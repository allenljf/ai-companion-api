"""路由層限流測試（source-spec 2.4：各端點獨立計數、429 回應）。"""

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_llm_client
from app.main import app
from app.services.llm.client import LLMClient, LLMProvider


class StubProvider(LLMProvider):
    async def chat(self, system, user, *, max_tokens, model, content_parts=None, json_mode=False, reasoning_effort=None):
        return "好的"


@pytest.fixture
def client() -> TestClient:
    app.dependency_overrides[get_llm_client] = lambda: LLMClient(
        providers={"stub": StubProvider()},
        routing={"self_introduction": ("stub", "m")},
    )
    yield TestClient(app)
    app.dependency_overrides.clear()


INTRO_URL = "/v1/companion/self-introduction"
INTRO_BODY = {
    "companion_name": "阿旅", "personality": "humorous",
    "speech_style": "friendly", "gender": "female",
}


def test_11th_request_within_minute_gets_429(client):
    headers = {"X-Forwarded-For": "203.0.113.9"}
    for _ in range(10):
        assert client.post(INTRO_URL, json=INTRO_BODY, headers=headers).status_code == 200
    res = client.post(INTRO_URL, json=INTRO_BODY, headers=headers)
    assert res.status_code == 429
    assert res.json()["metadata"]["status"] == "429001"


def test_routes_have_isolated_counters(client):
    # self-introduction 打滿 10 次後，未限流的 quiz-gallery 不受影響
    headers = {"X-Forwarded-For": "203.0.113.10"}
    for _ in range(10):
        client.post(INTRO_URL, json=INTRO_BODY, headers=headers)
    assert client.post(INTRO_URL, json=INTRO_BODY, headers=headers).status_code == 429
    assert client.get("/v1/companion/quiz-gallery", headers=headers).status_code == 200


def test_different_clients_have_isolated_counters(client):
    for _ in range(10):
        client.post(INTRO_URL, json=INTRO_BODY, headers={"X-Forwarded-For": "203.0.113.11"})
    res = client.post(INTRO_URL, json=INTRO_BODY, headers={"X-Forwarded-For": "203.0.113.12"})
    assert res.status_code == 200


def test_unthrottled_routes_have_no_limit(client):
    for _ in range(35):
        assert client.get("/v1/companion/ai-partner").status_code == 200
