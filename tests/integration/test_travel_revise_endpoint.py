"""POST /v1/plan/travel-revise 端到端（mock LLM）：400 契約 + 原樣返回 + 成功包裝（階段 5 驗收）。"""

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
        providers={"stub": provider}, routing={"travel_revise": ("stub", "stub-model")}
    )
    yield provider
    app.dependency_overrides.clear()


@pytest.fixture
def client(stub) -> TestClient:
    return TestClient(app)


URL = "/v1/plan/travel-revise"

DAY = {
    "day": 1,
    "status": "planned",
    "kind": "normal",
    "half_day": False,
    "items": [
        {"name": "清水寺", "text": "清晨人少", "type": "spot", "time": "09:00",
         "time_band": "上午", "lat": 34.99, "lng": 135.78}
    ],
}

VALID_BODY = {
    "itinerary": [DAY],
    "city": "京都",
    "messages": [{"role": "user", "content": "上午改成金閣寺"}],
}

LLM_OK = json.dumps(
    {
        "city": "京都",
        "reply": "改成金閣寺囉！",
        "off_topic": False,
        "changed_summary": "- Day 1 改為金閣寺",
        "unplanned_days": [],
        "pending_fields": [],
        "itinerary": [
            {
                "status": "planned", "kind": "normal", "half_day": False,
                "items": [
                    {"name": "金閣寺", "text": "金色輝映", "type": "spot", "time": "09:00",
                     "time_band": "上午", "lat": 35.03, "lng": 135.72}
                ],
            }
        ],
    },
    ensure_ascii=False,
)


class TestValidation400:
    def test_missing_itinerary(self, client):
        res = client.post(URL, json={"messages": VALID_BODY["messages"]})
        assert res.status_code == 400
        assert res.json()["metadata"]["status"] == "110001"

    def test_empty_itinerary(self, client):
        assert client.post(URL, json=VALID_BODY | {"itinerary": []}).status_code == 400

    def test_itinerary_max_30_days(self, client):
        assert (
            client.post(URL, json=VALID_BODY | {"itinerary": [DAY] * 31}).status_code == 400
        )

    def test_day_items_max_50(self, client):
        fat_day = DAY | {"items": [{"name": f"x{i}"} for i in range(51)]}
        assert client.post(URL, json=VALID_BODY | {"itinerary": [fat_day]}).status_code == 400

    def test_missing_messages(self, client):
        body = {k: v for k, v in VALID_BODY.items() if k != "messages"}
        assert client.post(URL, json=body).status_code == 400

    def test_messages_max_100(self, client):
        messages = [{"role": "user", "content": f"m{i}"} for i in range(101)]
        assert client.post(URL, json=VALID_BODY | {"messages": messages}).status_code == 400

    def test_message_bad_role(self, client):
        messages = [{"role": "system", "content": "hi"}]
        assert client.post(URL, json=VALID_BODY | {"messages": messages}).status_code == 400

    def test_target_day_must_be_positive(self, client):
        assert client.post(URL, json=VALID_BODY | {"target_day": 0}).status_code == 400

    def test_itinerary_items_not_deep_validated(self, client, stub):
        # 彈性原則：item 內部亂給不擋 400，由 normalize 層吸收
        stub.reply = LLM_OK
        weird = {"items": [{"oid": ["not-scalar"], "type": "hotel", "time": "25:99"}]}
        res = client.post(URL, json=VALID_BODY | {"itinerary": [weird]})
        assert res.status_code == 200


class TestUnchangedReturn:
    def test_llm_exception_returns_original_itinerary(self, client, stub):
        stub.reply = RuntimeError("boom")
        res = client.post(URL, json=VALID_BODY)
        assert res.status_code == 200
        data = res.json()["data"]
        assert data["fail_reason"] is not None
        days = data["itinerary_patch"]["days"]
        assert days and days[0]["items"][0]["name"] == "清水寺"
        assert data["itinerary_patch"]["changed_days"] == []

    def test_off_topic_returns_original_with_null_fail_reason(self, client, stub):
        stub.reply = json.dumps(
            {"reply": "我們來繼續調整行程吧！", "off_topic": True}, ensure_ascii=False
        )
        res = client.post(URL, json=VALID_BODY)
        data = res.json()["data"]
        assert data["off_topic"] is True
        assert data["fail_reason"] is None
        assert data["itinerary_patch"]["days"][0]["items"][0]["name"] == "清水寺"


class TestSuccess:
    def test_full_envelope(self, client, stub):
        stub.reply = LLM_OK
        res = client.post(URL, json=VALID_BODY)
        body = res.json()
        assert body["metadata"]["status"] == "0000"
        data = body["data"]
        assert data["fail_reason"] is None
        assert data["reply"] == "改成金閣寺囉！"
        assert data["changed_summary"] == "- Day 1 改為金閣寺"
        assert data["itinerary_patch"]["mode"] == "full"
        assert data["itinerary_patch"]["changed_days"] == [1]
        [day] = data["itinerary_patch"]["days"]
        assert day["day"] == 1 and day["items"][0]["name"] == "金閣寺"

    def test_existing_oid_survives_without_rebringing_orders(self, client, stub):
        day_with_oid = DAY | {
            "items": [DAY["items"][0] | {"oid": "O1"}]
        }
        stub.reply = json.dumps(
            {
                "city": "京都",
                "reply": "調整好了！",
                "off_topic": False,
                "itinerary": [
                    {"items": [{"name": "清水寺", "type": "spot", "time": "10:00", "oid": "O1"}]}
                ],
            },
            ensure_ascii=False,
        )
        res = client.post(URL, json=VALID_BODY | {"itinerary": [day_with_oid]})
        day = res.json()["data"]["itinerary_patch"]["days"][0]
        assert day["items"][0]["oid"] == "O1"
        assert day["booked_anchor"] == {"oids": ["O1"]}
