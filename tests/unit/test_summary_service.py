"""travel-summary service 四入口行為（mock LLM，對照 source-spec 5.1 / PHP requestSummary）。"""

import pytest

from app.services.llm.client import LLMClient, LLMProvider
from app.services.plan.summary import SOFT_FAIL_SUMMARY, summarize


class RecordingProvider(LLMProvider):
    def __init__(self, reply='{"summary": "LLM 寫的開場白", "city": "LLM 猜的城市"}'):
        self.reply = reply
        self.calls: list[dict] = []

    async def chat(self, system, user, *, max_tokens, model, content_parts=None, json_mode=False, reasoning_effort=None):
        self.calls.append(
            {
                "system": system,
                "user": user,
                "max_tokens": max_tokens,
                "model": model,
                "content_parts": content_parts,
                "json_mode": json_mode,
            }
        )
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


def make_client(provider: RecordingProvider) -> LLMClient:
    return LLMClient(providers={"p": provider}, routing={"travel_summary": ("p", "test-model")})


@pytest.fixture
def provider() -> RecordingProvider:
    return RecordingProvider()


class TestFromZero:
    @pytest.mark.asyncio
    async def test_no_llm_call_and_fixed_text(self, provider):
        result = await summarize({"entry_type": "from_zero"}, make_client(provider))

        assert provider.calls == []  # 不打 LLM
        assert result == {
            "summary": (
                "嗨，我是小旅！還沒有想法也沒關係，我們可以慢慢聊——想放鬆度假，還是想來點探險？"
                "先告訴我你想去哪，或想要什麼樣的旅行氣氛，我陪你從零開始排。"
            ),
            "city": "",
            "ai_model": None,
            "fail_reason": None,
        }

    @pytest.mark.asyncio
    async def test_uses_companion_name(self, provider):
        result = await summarize(
            {"entry_type": "from_zero", "companion_name": "阿旅"}, make_client(provider)
        )
        assert "嗨，我是阿旅！" in result["summary"]


class TestQuizCompletion:
    @pytest.mark.asyncio
    async def test_authority_city_overrides_llm_output(self, provider):
        result = await summarize(
            {"entry_type": "quiz_completion", "city": "大阪", "intro_text": "介紹文"},
            make_client(provider),
        )
        # LLM 回了 city 也不採信；權威輸入原樣回傳
        assert result["city"] == "大阪"
        assert result["summary"] == "LLM 寫的開場白"
        assert result["ai_model"] == "test-model"
        assert result["fail_reason"] is None

    @pytest.mark.asyncio
    async def test_prompt_and_user_message(self, provider):
        await summarize(
            {"entry_type": "quiz_completion", "city": "大阪", "companion_name": "阿旅"},
            make_client(provider),
        )
        call = provider.calls[0]
        assert "使用者剛完成旅行人格測驗" in call["system"]
        assert "你的名字是「阿旅」。" in call["system"]
        assert '"city": "大阪"' in call["user"]
        assert call["max_tokens"] == 1000
        assert call["json_mode"] is True

    @pytest.mark.asyncio
    async def test_llm_error_soft_fails_with_authority_city(self, provider):
        provider.reply = RuntimeError("boom")
        result = await summarize(
            {"entry_type": "quiz_completion", "city": "大阪"}, make_client(provider)
        )
        assert result["summary"] == SOFT_FAIL_SUMMARY
        assert result["city"] == "大阪"  # 軟失敗時 city 仍原樣回傳
        assert result["ai_model"] == "test-model"
        assert "RuntimeError" in result["fail_reason"]

    @pytest.mark.asyncio
    async def test_missing_summary_field_soft_fails(self, provider):
        provider.reply = '{"city": "只有城市沒有摘要"}'
        result = await summarize(
            {"entry_type": "quiz_completion", "city": "大阪"}, make_client(provider)
        )
        assert result["summary"] == SOFT_FAIL_SUMMARY
        assert "summary" in result["fail_reason"]


class TestFromOrders:
    @pytest.mark.asyncio
    async def test_authority_city_and_order_in_user_message(self, provider):
        result = await summarize(
            {
                "entry_type": "from_orders",
                "city": "東京",
                "order": {"prod_name": "門票", "go_dt": "2026-09-02"},
            },
            make_client(provider),
        )
        assert result["city"] == "東京"
        call = provider.calls[0]
        assert "從他即將出發的訂單中" in call["system"]
        assert '"prod_name": "門票"' in call["user"]


class TestImportedItinerary:
    @pytest.mark.asyncio
    async def test_city_comes_from_llm(self, provider):
        result = await summarize(
            {"entry_type": "imported_itinerary", "source_type": "text", "content": "Day 1 清水寺"},
            make_client(provider),
        )
        assert result["city"] == "LLM 猜的城市"
        assert '"content": "Day 1 清水寺"' in provider.calls[0]["user"]
        assert provider.calls[0]["content_parts"] is None

    @pytest.mark.asyncio
    async def test_missing_city_in_llm_output_becomes_empty(self, provider):
        provider.reply = '{"summary": "看懂了"}'
        result = await summarize(
            {"entry_type": "imported_itinerary", "source_type": "text", "content": "x"},
            make_client(provider),
        )
        assert result["city"] == ""
        assert result["fail_reason"] is None

    @pytest.mark.asyncio
    async def test_image_source_builds_content_parts(self, provider):
        await summarize(
            {
                "entry_type": "imported_itinerary",
                "source_type": "image",
                "image_urls": ["https://x/1.png", "https://x/2.png"],
            },
            make_client(provider),
        )
        parts = provider.calls[0]["content_parts"]
        assert parts[0]["type"] == "text"
        assert '"has_image": true' in parts[0]["text"]
        assert parts[1] == {"type": "image_url", "image_url": {"url": "https://x/1.png"}}
        assert parts[2] == {"type": "image_url", "image_url": {"url": "https://x/2.png"}}

    @pytest.mark.asyncio
    async def test_soft_fail_city_is_empty(self, provider):
        provider.reply = RuntimeError("boom")
        result = await summarize(
            {"entry_type": "imported_itinerary", "source_type": "text", "content": "x"},
            make_client(provider),
        )
        assert result["city"] == ""
        assert result["summary"] == SOFT_FAIL_SUMMARY
