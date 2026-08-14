"""travel-summary-from-history：獨立實作（不與 wish 共用），防呆行為必須等價（source-spec 5.4 / 8.8）。"""

import pytest

from app.services.llm.client import LLMClient, LLMProvider
from app.services.plan.summary_from_history import (
    build_user_message,
    fallback_greeting,
    normalize_cities,
    summarize_from_history,
)

PRODUCTS = [
    {"prod_id": "H100", "prod_name": "上高地健行一日遊", "destination_names": ["上高地"]},
    {"prod_id": "H200", "prod_name": "大阪環球影城門票", "destination_names": ["大阪"]},
]


class TestNormalizeCities:
    def test_out_of_range_and_duplicate_index_defenses(self):
        decoded = {
            "cities": [
                {"city": "松本", "product_indexes": [0, 5, "x", 0]},
                {"city": "大阪", "product_indexes": [0, 1]},
            ]
        }
        out = normalize_cities(decoded, PRODUCTS)
        assert out == [
            {"city": "松本", "products": [{"prod_id": "H100", "prod_name": "上高地健行一日遊"}]},
            {"city": "大阪", "products": [{"prod_id": "H200", "prod_name": "大阪環球影城門票"}]},
        ]

    def test_llm_error_and_low_confidence_split(self):
        assert normalize_cities({"nope": 1}, PRODUCTS) is None
        assert normalize_cities({"cities": []}, PRODUCTS) == []

    def test_city_dedup_and_cap(self):
        decoded = {
            "cities": [{"city": c, "product_indexes": []} for c in ["A", "a", "B", "C", "D"]]
        }
        assert [c["city"] for c in normalize_cities(decoded, PRODUCTS)] == ["A", "B", "C"]


class TestBuildUserMessage:
    def test_prod_id_never_in_prompt(self):
        msg = build_user_message(PRODUCTS)
        assert "H100" not in msg and "H200" not in msg and "prod_id" not in msg


class RecordingProvider(LLMProvider):
    def __init__(self, reply):
        self.reply = reply
        self.calls: list[dict] = []

    async def chat(self, system, user, *, max_tokens, model, content_parts=None, json_mode=False, reasoning_effort=None):
        self.calls.append({"system": system, "max_tokens": max_tokens})
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


def make_client(provider) -> LLMClient:
    return LLMClient(
        providers={"p": provider}, routing={"travel_summary_from_history": ("p", "test-model")}
    )


class TestSummarizeFromHistory:
    @pytest.mark.asyncio
    async def test_success_with_history_wording(self):
        provider = RecordingProvider(
            '{"greeting": "看了你最近瀏覽的行程！", "cities": [{"city": "松本", "product_indexes": [0]}]}'
        )
        result = await summarize_from_history({"products": PRODUCTS}, make_client(provider))
        assert result["fail_reason"] is None
        assert result["cities"][0]["products"][0]["prod_id"] == "H100"
        assert "瀏覽與購買紀錄判讀引擎" in provider.calls[0]["system"]
        assert provider.calls[0]["max_tokens"] == 800

    @pytest.mark.asyncio
    async def test_fallback_mentions_history_not_wish(self):
        provider = RecordingProvider(RuntimeError("boom"))
        result = await summarize_from_history({"products": PRODUCTS}, make_client(provider))
        assert result["fail_reason"] == "llm_error"
        assert "瀏覽紀錄" in result["greeting"]
        assert "願望清單" not in result["greeting"]

    @pytest.mark.asyncio
    async def test_empty_cities_low_confidence(self):
        provider = RecordingProvider('{"greeting": "hi", "cities": []}')
        result = await summarize_from_history({"products": PRODUCTS}, make_client(provider))
        assert result["fail_reason"] == "low_confidence"

    @pytest.mark.asyncio
    async def test_blank_greeting_falls_back(self):
        provider = RecordingProvider('{"greeting": "", "cities": [{"city": "松本"}]}')
        result = await summarize_from_history({"products": PRODUCTS}, make_client(provider))
        assert result["greeting"] == fallback_greeting({})
        assert result["fail_reason"] is None
