"""Phase 1 文字三支端到端（mock LLM）：400 契約 + 軟失敗 + 成功包裝（階段 7 驗收）。"""

import json

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_kv, get_llm_client
from app.main import app
from app.services.llm.client import LLMClient, LLMProvider
from app.storage.kv import InMemoryKV


class StubProvider(LLMProvider):
    def __init__(self):
        self.reply = "{}"

    async def chat(self, system, user, *, max_tokens, model, content_parts=None, json_mode=False, reasoning_effort=None):
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply if not callable(self.reply) else self.reply(user)


@pytest.fixture
def kv() -> InMemoryKV:
    return InMemoryKV()


@pytest.fixture
def stub(kv) -> StubProvider:
    provider = StubProvider()
    routing = {
        "quiz": ("stub", "stub-model"),
        "quiz_completions": ("stub", "stub-model"),
        "self_introduction": ("stub", "stub-model"),
    }
    app.dependency_overrides[get_llm_client] = lambda: LLMClient(
        providers={"stub": provider}, routing=routing
    )
    app.dependency_overrides[get_kv] = lambda: kv
    yield provider
    app.dependency_overrides.clear()


@pytest.fixture
def client(stub) -> TestClient:
    return TestClient(app)


# ---------------------------------------------------------------------------
# POST /v1/companion/quiz
# ---------------------------------------------------------------------------

QUIZ_URL = "/v1/companion/quiz"
QUIZ_BODY = {"shown_question_counts": {}, "personality": "humorous", "speech_style": "friendly"}


class TestQuiz:
    def test_missing_fields_400(self, client):
        assert client.post(QUIZ_URL, json={}).status_code == 400
        assert client.post(QUIZ_URL, json={"personality": "x"}).status_code == 400

    def test_counts_must_be_positive_ints(self, client):
        body = QUIZ_BODY | {"shown_question_counts": {"1-1": 0}}
        assert client.post(QUIZ_URL, json=body).status_code == 400
        body = QUIZ_BODY | {"shown_question_counts": {"1-1": "abc"}}
        assert client.post(QUIZ_URL, json=body).status_code == 400

    def test_llm_failure_still_returns_questions(self, client, stub):
        stub.reply = RuntimeError("boom")
        res = client.post(QUIZ_URL, json=QUIZ_BODY)
        assert res.status_code == 200
        data = res.json()["data"]
        assert data["fail_reason"] is not None
        assert data["count"] == 8 and len(data["questions"]) == 8
        for q in data["questions"]:
            assert q["text"]
            for o in q["options"]:
                assert "tag_id" not in o
                assert o["tag"]["label"]
                assert o["image_url"].startswith("https://storage.googleapis.com/")

    def test_success_envelope(self, client, stub):
        def rewrite(user_message: str) -> str:
            payload = json.loads(
                user_message[len("<<<USER_INPUT\n") : -len("\n>>>END_USER_INPUT")]
            )
            return json.dumps(
                {"questions": [
                    {"id": q["id"], "text": f"改{q['id']}",
                     "options": [{"index": o["index"], "text": f"選{o['index']}"} for o in q["options"]]}
                    for q in payload["questions"]
                ]},
                ensure_ascii=False,
            )

        stub.reply = rewrite
        res = client.post(QUIZ_URL, json=QUIZ_BODY)
        data = res.json()["data"]
        assert data["fail_reason"] is None
        assert all(q["text"].startswith("改") for q in data["questions"])


# ---------------------------------------------------------------------------
# POST /v1/companion/quiz-completions
# ---------------------------------------------------------------------------

COMPLETION_URL = "/v1/companion/quiz-completions"
COMPLETION_BODY = {
    "completion_uuid": "b0e7a3a8-8f2f-4c1e-9d2f-1c9a35c1d001",
    "personality": "humorous",
    "speech_style": "friendly",
    "selected_tags": ["t1-2", "t2-4", "t5-1", "t7-1", "t8-1"],  # → A1 獨處療癒師
    "shown_cities": [],
    "companion_name": "阿旅",
}

COMPLETION_LLM_OK = json.dumps(
    {
        "travel_identity": "亂給", "travel_identity_en": "Wrong",
        "destination_cn": "清邁", "destination_en": "Chiang Mai",
        "destination_country": "泰國", "destination_country_en": "Thailand",
        "tagline": "去山裡充電", "tagline_en": "Recharge in the hills",
        "highlight_tags": ["身心療癒派", "大自然充電族", "安心舒適圈"],
        "highlight_tags_en": ["Healer", "Nature", "Comfort"],
        "companion_quote": "慢慢走，才看得到風景。", "companion_quote_en": "Slow down.",
        "reasoning": ["嗯，你想安靜…", "啊，有了！", "清邁，剛剛好"],
        "recommendation": ["第一段。", "第二段。", "第三段。"],
        "social_post": "清邁充電之旅 #KKday",
    },
    ensure_ascii=False,
)


class TestQuizCompletions:
    def test_invalid_uuid_400(self, client):
        assert (
            client.post(COMPLETION_URL, json=COMPLETION_BODY | {"completion_uuid": "not-uuid"}).status_code
            == 400
        )

    def test_empty_selected_tags_400(self, client):
        assert (
            client.post(COMPLETION_URL, json=COMPLETION_BODY | {"selected_tags": []}).status_code == 400
        )

    def test_missing_shown_cities_400(self, client):
        body = {k: v for k, v in COMPLETION_BODY.items() if k != "shown_cities"}
        assert client.post(COMPLETION_URL, json=body).status_code == 400

    def test_success_identity_overridden_and_cached(self, client, stub, kv):
        stub.reply = COMPLETION_LLM_OK
        res = client.post(COMPLETION_URL, json=COMPLETION_BODY)
        data = res.json()["data"]
        assert data["fail_reason"] is None
        assert data["travel_identity"] == "獨處療癒師"      # mapping 覆寫
        assert data["share_image_status"] == "pending"
        assert data["destination_cn"] == "清邁"
        cached = kv.get("companion:completion:" + COMPLETION_BODY["completion_uuid"])
        assert cached["analysis"]["travel_identity"] == "獨處療癒師"

    def test_llm_failure_soft_fails_without_cache(self, client, stub, kv):
        stub.reply = RuntimeError("boom")
        res = client.post(COMPLETION_URL, json=COMPLETION_BODY)
        assert res.status_code == 200
        data = res.json()["data"]
        assert data["fail_reason"] is not None
        assert data["share_image_status"] == "skipped"
        assert data["travel_identity"] == "獨處療癒師"      # 規則式判定不依賴 LLM
        assert kv.get("companion:completion:" + COMPLETION_BODY["completion_uuid"]) is None


# ---------------------------------------------------------------------------
# POST /v1/companion/self-introduction
# ---------------------------------------------------------------------------

INTRO_URL = "/v1/companion/self-introduction"
INTRO_BODY = {
    "companion_name": "阿旅",
    "personality": "humorous",
    "speech_style": "friendly",
    "gender": "female",
}


class TestSelfIntroduction:
    def test_tag_not_in_partner_options_400(self, client):
        # 動態比對 data/ai_partner.json 的選項清單（對照 PHP Rule::in）
        assert client.post(INTRO_URL, json=INTRO_BODY | {"personality": "nope"}).status_code == 400
        assert client.post(INTRO_URL, json=INTRO_BODY | {"speech_style": "nope"}).status_code == 400
        assert client.post(INTRO_URL, json=INTRO_BODY | {"gender": "robot"}).status_code == 400

    def test_missing_companion_name_400(self, client):
        body = {k: v for k, v in INTRO_BODY.items() if k != "companion_name"}
        assert client.post(INTRO_URL, json=body).status_code == 400

    def test_success(self, client, stub):
        stub.reply = "嗨！我是阿旅～走，出發！"
        data = client.post(INTRO_URL, json=INTRO_BODY).json()["data"]
        assert data["introduction"] == "嗨！我是阿旅～走，出發！"
        assert data["fail_reason"] is None

    def test_llm_failure_falls_back(self, client, stub):
        stub.reply = RuntimeError("boom")
        res = client.post(INTRO_URL, json=INTRO_BODY)
        assert res.status_code == 200
        data = res.json()["data"]
        assert data["fail_reason"] is not None
        assert data["introduction"].startswith("嗨！我是阿旅")
