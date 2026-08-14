"""travel-summary-from-wish：聚合 + 位置索引對映防呆（source-spec 5.3 / 8.8，技法 2）。"""

import json

import pytest

from app.services.llm.client import LLMClient, LLMProvider
from app.services.plan.summary_from_wish import (
    build_user_message,
    fallback_greeting,
    normalize_cities,
    summarize_from_wish,
)

PRODUCTS = [
    {"prod_id": "P100", "prod_name": "新天鵝堡之旅", "destination_names": ["新天鵝堡"]},
    {"prod_id": "P200", "prod_name": "馬德里一日遊", "introduction": "含博物館門票"},
    {"prod_id": "P300", "prod_name": "慕尼黑啤酒節"},
]


class TestNormalizeCities:
    def test_valid_aggregation(self):
        decoded = {
            "cities": [
                {"city": "慕尼黑", "product_indexes": [0, 2]},
                {"city": "馬德里", "product_indexes": [1]},
            ]
        }
        assert normalize_cities(decoded, PRODUCTS) == [
            {
                "city": "慕尼黑",
                "products": [
                    {"prod_id": "P100", "prod_name": "新天鵝堡之旅"},
                    {"prod_id": "P300", "prod_name": "慕尼黑啤酒節"},
                ],
            },
            {"city": "馬德里", "products": [{"prod_id": "P200", "prod_name": "馬德里一日遊"}]},
        ]

    def test_missing_cities_key_is_llm_error(self):
        assert normalize_cities({"greeting": "hi"}, PRODUCTS) is None
        assert normalize_cities(None, PRODUCTS) is None
        assert normalize_cities({"cities": "慕尼黑"}, PRODUCTS) is None

    def test_empty_cities_list_returns_empty(self):
        # 空陣列 → 呼叫端判 low_confidence（不是 llm_error）
        assert normalize_cities({"cities": []}, PRODUCTS) == []

    def test_out_of_range_index_ignored_no_fake_prod_id(self):
        # ★ 階段 2 驗收要點：超範圍索引不 crash、不產出假 prod_id
        decoded = {"cities": [{"city": "慕尼黑", "product_indexes": [0, 99, -1, 2]}]}
        out = normalize_cities(decoded, PRODUCTS)
        assert out == [
            {
                "city": "慕尼黑",
                "products": [
                    {"prod_id": "P100", "prod_name": "新天鵝堡之旅"},
                    {"prod_id": "P300", "prod_name": "慕尼黑啤酒節"},
                ],
            }
        ]
        all_ids = {p["prod_id"] for c in out for p in c["products"]}
        assert all_ids <= {"P100", "P200", "P300"}

    def test_non_numeric_index_ignored(self):
        decoded = {"cities": [{"city": "慕尼黑", "product_indexes": ["abc", None, [], 0]}]}
        out = normalize_cities(decoded, PRODUCTS)
        assert out[0]["products"] == [{"prod_id": "P100", "prod_name": "新天鵝堡之旅"}]

    def test_duplicate_index_across_cities_only_first_counts(self):
        # 同一商品不該同時掛在兩個城市底下
        decoded = {
            "cities": [
                {"city": "慕尼黑", "product_indexes": [0]},
                {"city": "馬德里", "product_indexes": [0, 1]},
            ]
        }
        out = normalize_cities(decoded, PRODUCTS)
        assert out[0]["products"] == [{"prod_id": "P100", "prod_name": "新天鵝堡之旅"}]
        assert out[1]["products"] == [{"prod_id": "P200", "prod_name": "馬德里一日遊"}]

    def test_duplicate_index_within_city_only_first_counts(self):
        decoded = {"cities": [{"city": "慕尼黑", "product_indexes": [0, 0, 0]}]}
        out = normalize_cities(decoded, PRODUCTS)
        assert len(out[0]["products"]) == 1

    def test_city_dedup_case_insensitive(self):
        decoded = {
            "cities": [
                {"city": "Kyoto", "product_indexes": [0]},
                {"city": "kyoto", "product_indexes": [1]},
            ]
        }
        out = normalize_cities(decoded, PRODUCTS)
        assert len(out) == 1
        assert out[0]["city"] == "Kyoto"

    def test_max_three_cities(self):
        decoded = {
            "cities": [
                {"city": f"城市{i}", "product_indexes": []} for i in range(5)
            ]
        }
        out = normalize_cities(decoded, PRODUCTS)
        assert [c["city"] for c in out] == ["城市0", "城市1", "城市2"]

    def test_empty_or_invalid_city_skipped(self):
        decoded = {
            "cities": [
                {"city": "", "product_indexes": [0]},
                {"city": None, "product_indexes": [1]},
                {"product_indexes": [1]},
                {"city": "慕尼黑", "product_indexes": [2]},
            ]
        }
        out = normalize_cities(decoded, PRODUCTS)
        assert [c["city"] for c in out] == ["慕尼黑"]

    def test_city_truncated_to_20_chars(self):
        decoded = {"cities": [{"city": "超" * 30, "product_indexes": []}]}
        assert normalize_cities(decoded, PRODUCTS)[0]["city"] == "超" * 20

    def test_missing_product_indexes_treated_as_empty(self):
        decoded = {"cities": [{"city": "慕尼黑"}]}
        assert normalize_cities(decoded, PRODUCTS) == [{"city": "慕尼黑", "products": []}]

    def test_city_entry_not_a_dict_skipped(self):
        decoded = {"cities": ["慕尼黑", {"city": "馬德里", "product_indexes": []}]}
        out = normalize_cities(decoded, PRODUCTS)
        assert [c["city"] for c in out] == ["馬德里"]


class TestBuildUserMessage:
    def test_prod_id_never_in_prompt(self):
        # ★ 識別碼絕不進 prompt（技法 2 的核心）
        msg = build_user_message(PRODUCTS)
        assert "P100" not in msg and "P200" not in msg and "P300" not in msg
        assert "prod_id" not in msg

    def test_explicit_index_field(self):
        msg = build_user_message(PRODUCTS)
        payload = json.loads(msg[len("<<<USER_INPUT\n") : -len("\n>>>END_USER_INPUT")])
        assert [p["index"] for p in payload["products"]] == [0, 1, 2]
        assert payload["products"][0] == {
            "index": 0,
            "prod_name": "新天鵝堡之旅",
            "introduction": "",
            "destination_names": ["新天鵝堡"],
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
        providers={"p": provider}, routing={"travel_summary_from_wish": ("p", "test-model")}
    )


class TestSummarizeFromWish:
    @pytest.mark.asyncio
    async def test_success(self):
        provider = RecordingProvider(
            '{"greeting": "看了你的收藏！", "cities": [{"city": "慕尼黑", "product_indexes": [0, 2]}]}'
        )
        result = await summarize_from_wish({"products": PRODUCTS}, make_client(provider))
        assert result["fail_reason"] is None
        assert result["greeting"] == "看了你的收藏！"
        assert result["cities"][0]["city"] == "慕尼黑"
        assert result["ai_model"] == "test-model"
        assert provider.calls[0]["max_tokens"] == 800

    @pytest.mark.asyncio
    async def test_llm_exception_is_llm_error(self):
        provider = RecordingProvider(RuntimeError("boom"))
        result = await summarize_from_wish({"products": PRODUCTS}, make_client(provider))
        assert result["fail_reason"] == "llm_error"
        assert result["cities"] == []
        assert "願望清單" in result["greeting"]

    @pytest.mark.asyncio
    async def test_empty_cities_is_low_confidence(self):
        provider = RecordingProvider('{"greeting": "hi", "cities": []}')
        result = await summarize_from_wish({"products": PRODUCTS}, make_client(provider))
        assert result["fail_reason"] == "low_confidence"
        assert result["cities"] == []

    @pytest.mark.asyncio
    async def test_all_cities_invalid_is_low_confidence(self):
        provider = RecordingProvider('{"greeting": "hi", "cities": [{"city": ""}]}')
        result = await summarize_from_wish({"products": PRODUCTS}, make_client(provider))
        assert result["fail_reason"] == "low_confidence"

    @pytest.mark.asyncio
    async def test_wish_wording_in_prompt(self):
        provider = RecordingProvider('{"greeting": "hi", "cities": [{"city": "慕尼黑"}]}')
        await summarize_from_wish({"products": PRODUCTS}, make_client(provider))
        assert "願望清單判讀引擎" in provider.calls[0]["system"]

    @pytest.mark.asyncio
    async def test_fallback_greeting_when_blank(self):
        provider = RecordingProvider('{"greeting": "", "cities": [{"city": "慕尼黑"}]}')
        result = await summarize_from_wish(
            {"products": PRODUCTS, "companion_name": "阿旅"}, make_client(provider)
        )
        assert result["greeting"] == fallback_greeting({"companion_name": "阿旅"})
        assert result["fail_reason"] is None
