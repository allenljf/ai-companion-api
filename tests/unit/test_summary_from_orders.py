"""travel-summary-from-orders：逐筆對應 + 後端指定 order_index（source-spec 5.2 / 8.7）。"""

import json

import pytest

from app.services.llm.client import LLMClient, LLMProvider
from app.services.plan.summary_from_orders import (
    build_user_message,
    fallback_greeting,
    normalize_cities,
    summarize_from_orders,
)


class TestNormalizeCities:
    def test_valid_response(self):
        decoded = {"greeting": "hi", "cities": ["東京", "洛杉磯", "小樽"]}
        assert normalize_cities(decoded, 3) == ["東京", "洛杉磯", "小樽"]

    def test_count_mismatch_returns_none(self):
        # 筆數不符 = llm_error（位置對應的前提破了，寧可整包失敗）
        assert normalize_cities({"cities": ["東京"]}, 2) is None
        assert normalize_cities({"cities": ["東京", "大阪", "京都"]}, 2) is None

    def test_missing_cities_key_returns_none(self):
        assert normalize_cities({"greeting": "hi"}, 1) is None
        assert normalize_cities(None, 1) is None

    def test_cities_not_a_list_returns_none(self):
        assert normalize_cities({"cities": "東京"}, 1) is None

    def test_decoded_is_list_returns_none(self):
        # LLM 直接回陣列（沒有物件包裝）也不能 crash
        assert normalize_cities(["東京"], 1) is None

    def test_non_string_city_becomes_empty(self):
        assert normalize_cities({"cities": [123, None, "東京"]}, 3) == ["", "", "東京"]

    def test_city_trimmed_and_truncated_to_20_chars(self):
        long_city = "超" * 30
        out = normalize_cities({"cities": [f"  東京  ", long_city]}, 2)
        assert out == ["東京", "超" * 20]


class TestBuildUserMessage:
    def test_only_three_fields_per_order(self):
        msg = build_user_message(
            [{"prod_name": "門票", "package_name": "1日券", "destination_name": "東京", "oid": "O123"}]
        )
        payload = json.loads(msg[len("<<<USER_INPUT\n") : -len("\n>>>END_USER_INPUT")])
        # oid 之類的識別碼絕不進 prompt
        assert payload == {
            "orders": [{"prod_name": "門票", "package_name": "1日券", "destination_name": "東京"}]
        }


class RecordingProvider(LLMProvider):
    def __init__(self, reply):
        self.reply = reply
        self.calls: list[dict] = []

    async def chat(self, system, user, *, max_tokens, model, content_parts=None, json_mode=False, reasoning_effort=None):
        self.calls.append({"system": system, "user": user, "max_tokens": max_tokens})
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


def make_client(provider) -> LLMClient:
    return LLMClient(
        providers={"p": provider},
        routing={"travel_summary_from_orders": ("p", "test-model")},
    )


ORDERS_2 = [
    {"prod_name": "晴空塔門票", "destination_name": "東京"},
    {"prod_name": "環球影城門票", "destination_name": "美國"},
]


class TestSummarizeFromOrders:
    @pytest.mark.asyncio
    async def test_success_builds_options_with_backend_indexes(self):
        provider = RecordingProvider('{"greeting": "有旅程囉！", "cities": ["東京", "洛杉磯"]}')
        result = await summarize_from_orders({"orders": ORDERS_2}, make_client(provider))

        assert result == {
            "greeting": "有旅程囉！",
            "options": [
                {"order_index": 0, "city": "東京"},
                {"order_index": 1, "city": "洛杉磯"},
            ],
            "ai_model": "test-model",
            "fail_reason": None,
        }
        assert provider.calls[0]["max_tokens"] == 400

    @pytest.mark.asyncio
    async def test_empty_city_kept_in_position(self):
        # 判斷不出的那筆保留空字串（位置不能亂）
        provider = RecordingProvider('{"greeting": "hi", "cities": ["東京", ""]}')
        result = await summarize_from_orders({"orders": ORDERS_2}, make_client(provider))
        assert result["options"] == [
            {"order_index": 0, "city": "東京"},
            {"order_index": 1, "city": ""},
        ]
        assert result["fail_reason"] is None

    @pytest.mark.asyncio
    async def test_count_mismatch_is_llm_error(self):
        provider = RecordingProvider('{"greeting": "hi", "cities": ["東京"]}')
        result = await summarize_from_orders({"orders": ORDERS_2}, make_client(provider))
        assert result["fail_reason"] == "llm_error"
        assert result["options"] == []
        assert "沒辦法從你的訂單判斷出目的地" in result["greeting"]

    @pytest.mark.asyncio
    async def test_all_empty_is_low_confidence(self):
        provider = RecordingProvider('{"greeting": "hi", "cities": ["", ""]}')
        result = await summarize_from_orders({"orders": ORDERS_2}, make_client(provider))
        assert result["fail_reason"] == "low_confidence"
        assert result["options"] == []

    @pytest.mark.asyncio
    async def test_llm_exception_is_llm_error(self):
        provider = RecordingProvider(RuntimeError("boom"))
        result = await summarize_from_orders({"orders": ORDERS_2}, make_client(provider))
        assert result["fail_reason"] == "llm_error"
        assert result["ai_model"] == "test-model"

    @pytest.mark.asyncio
    async def test_blank_greeting_falls_back(self):
        provider = RecordingProvider('{"greeting": "  ", "cities": ["東京", "洛杉磯"]}')
        result = await summarize_from_orders(
            {"orders": ORDERS_2, "companion_name": "阿旅"}, make_client(provider)
        )
        assert result["greeting"] == fallback_greeting({"companion_name": "阿旅"})
        assert "阿旅" in result["greeting"]
        assert result["fail_reason"] is None  # greeting 兜底不算失敗

    @pytest.mark.asyncio
    async def test_persona_and_name_in_system_prompt(self):
        provider = RecordingProvider('{"greeting": "hi", "cities": ["東京", "洛杉磯"]}')
        await summarize_from_orders(
            {"orders": ORDERS_2, "companion_name": "阿旅", "personality": "幽默"},
            make_client(provider),
        )
        system = provider.calls[0]["system"]
        assert "訂單判讀引擎" in system
        assert "你的名字是「阿旅」，人格特質與說話風格關鍵字：幽默" in system
        assert "我是阿旅！" in system  # few-shot 範例的 greeting 用實際名字
