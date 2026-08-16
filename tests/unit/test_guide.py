"""travel-guide service 測試（source-spec 5.6 / 8.10，mock LLM）。"""

import json

import pytest

from app.core.prompt_loader import render_prompt
from app.services.llm.client import LLMClient, LLMProvider
from app.services.plan.guide import (
    build_system_prompt,
    build_user_message,
    generate,
    normalize_messages,
)

ORDER = {
    "oid": "26KK216164788",
    "prod_name": "大阪環球影城門票",
    "package_name": "一日券",
    "destination_name": "大阪",
    "go_dt": "2026-09-01",
}
PRODUCT = {
    "prod_id": "157138",
    "prod_name": "新天鵝堡一日遊",
    "introduction": "含交通接駁",
    "destination_names": ["慕尼黑"],
}


# ---------------------------------------------------------------------------
# prompt 組裝：條件注入段（沒帶時與基礎版完全相同）
# ---------------------------------------------------------------------------


BASE_PARAMS = {"summary": "想去京都放鬆", "city": "京都", "preferences": {"duration": "3天"}}


class TestBuildSystemPrompt:
    def test_prompt_has_no_orders_or_products_sections(self):
        # 2026-08-16：本 API 不再接受 orders/products，prompt 也不再提及
        prompt = build_system_prompt(BASE_PARAMS)
        assert "已預訂項目" not in prompt and "使用者挑選的商品" not in prompt
        assert "orders" not in prompt and "products" not in prompt
        assert "你是 KKday「AI 旅伴」的旅遊行程規劃引擎。" in prompt
        assert "11. 全文使用繁體中文。" in prompt
        assert "只輸出純 JSON" in prompt

    def test_matches_template_with_persona_only(self):
        from app.services.plan.persona import persona_text

        expected = render_prompt("phase2/guide", persona=persona_text(BASE_PARAMS))
        assert build_system_prompt(BASE_PARAMS) == expected

    def test_persona_injected(self):
        prompt = build_system_prompt(BASE_PARAMS | {"companion_name": "阿旅", "personality": "幽默"})
        assert "你的名字是「阿旅」，人格特質與說話風格關鍵字：幽默" in prompt


class TestBuildUserMessage:
    def _payload(self, params: dict) -> dict:
        msg = build_user_message(params)
        return json.loads(msg[len("<<<USER_INPUT\n") : -len("\n>>>END_USER_INPUT")])

    def test_base_payload(self):
        payload = self._payload(BASE_PARAMS)
        assert payload == {
            "summary": "想去京都放鬆",
            "city": "京都",
            "preferences": {"duration": "3天"},
        }
        assert "orders" not in payload and "products" not in payload

    def test_orders_and_products_never_sent(self):
        # 即使 App 誤帶，也不會進 prompt（schema 層已擋，這裡是雙保險）
        payload = self._payload(BASE_PARAMS | {"orders": [ORDER], "products": [PRODUCT]})
        assert "orders" not in payload and "products" not in payload


# ---------------------------------------------------------------------------
# messages 正規化：最多 2 則、≤60 字句尾截斷、type 白名單、空給預設
# ---------------------------------------------------------------------------


class TestNormalizeMessages:
    def test_caps_at_two_messages(self):
        messages = normalize_messages(
            [{"type": "statement", "text": f"訊息{i}"} for i in range(4)]
        )
        assert len(messages) == 2

    def test_truncates_over_60_chars_at_sentence_end(self):
        long_text = "第一句話說完了。" + "後面這段會超過六十個字所以要被截斷" * 5
        [message] = normalize_messages([{"type": "statement", "text": long_text}])
        assert len(message["text"]) <= 60
        assert message["text"].endswith("。")

    def test_invalid_type_becomes_statement(self):
        [message] = normalize_messages([{"type": "question", "text": "hi"}])
        assert message["type"] == "statement"

    def test_valid_types_kept(self):
        messages = normalize_messages(
            [{"type": "completion", "text": "a"}, {"type": "statement", "text": "b"}]
        )
        assert [m["type"] for m in messages] == ["completion", "statement"]

    def test_plain_string_message_accepted(self):
        [message] = normalize_messages(["行程排好囉"])
        assert message == {"type": "statement", "text": "行程排好囉"}

    def test_empty_messages_get_default_completion(self):
        assert normalize_messages([]) == [{"type": "completion", "text": "行程排好了！"}]
        assert normalize_messages([{"type": "statement", "text": "  "}]) == [
            {"type": "completion", "text": "行程排好了！"}
        ]


# ---------------------------------------------------------------------------
# generate：端到端（mock LLM）
# ---------------------------------------------------------------------------


class StubProvider(LLMProvider):
    def __init__(self, reply):
        self.reply = reply
        self.calls: list[dict] = []

    async def chat(self, system, user, *, max_tokens, model, content_parts=None, json_mode=False, reasoning_effort=None):
        self.calls.append({"system": system, "user": user, "max_tokens": max_tokens})
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


def make_client(reply) -> tuple[LLMClient, StubProvider]:
    provider = StubProvider(reply)
    client = LLMClient(
        providers={"stub": provider}, routing={"travel_guide": ("stub", "guide-model")}
    )
    return client, provider


def llm_reply(**overrides) -> str:
    body = {
        "city": "京都",
        "days": 2,
        "date_range": None,
        "messages": [{"type": "completion", "text": "行程排好了！"}],
        "unplanned_days": [],
        "pending_fields": [],
        "itinerary": [
            {
                "day": 1,
                "status": "planned",
                "kind": "normal",
                "half_day": False,
                "items": [
                    {"name": "清水寺", "text": "清晨人少", "type": "spot",
                     "time": "09:00", "time_band": "上午", "lat": 34.99, "lng": 135.78},
                ],
            },
            {
                "day": 2,
                "status": "planned",
                "kind": "normal",
                "half_day": False,
                "items": [],
            },
        ],
    } | overrides
    return json.dumps(body, ensure_ascii=False)


@pytest.mark.asyncio
async def test_generate_success_envelope():
    client, provider = make_client(llm_reply())
    result = await generate(dict(BASE_PARAMS), client)

    assert result["fail_reason"] is None
    assert result["city"] == "京都"
    assert result["days"] == 2
    assert result["phase"] == "done"
    assert result["unplanned_days"] == []
    assert result["itinerary_patch"]["mode"] == "full"
    assert result["itinerary_patch"]["changed_days"] == [1, 2]
    assert [d["day"] for d in result["itinerary_patch"]["days"]] == [1, 2]
    assert result["progress_label"] == "已排 2/2 天"
    assert result["chips"] == {"mode": "hidden", "items": []}
    assert result["main_action"] == {"type": "view_trip", "label": "看看完整行程"}
    assert result["ai_model"] == "guide-model"
    assert result["days_provisional"] is True
    # GUIDE_MAX_TOKENS：完整行程 JSON 較大，給足 token
    assert provider.calls[0]["max_tokens"] == 8000


@pytest.mark.asyncio
async def test_generate_city_mismatch_is_failure():
    client, _ = make_client(llm_reply(city="大阪"))
    result = await generate(dict(BASE_PARAMS), client)
    assert result["fail_reason"] is not None
    assert "大阪" in result["fail_reason"] and "京都" in result["fail_reason"]
    # 失敗兜底：可渲染、不回半新半舊
    assert result["city"] is None
    assert result["itinerary_patch"] == {"mode": "full", "changed_days": [], "days": []}
    assert result["messages"] == [
        {"type": "statement", "text": "嗯…行程排到一半卡住了，要不要再試一次？"}
    ]
    assert result["phase"] == "plan" and result["main_action"] is None


@pytest.mark.asyncio
async def test_generate_simplified_city_not_false_failure():
    # LLM 吐簡體「京都」同字，但如「东京」vs「東京」需先轉繁再比對
    client, _ = make_client(llm_reply(city="东京"))
    result = await generate(dict(BASE_PARAMS) | {"city": "東京"}, client)
    assert result["fail_reason"] is None
    assert result["city"] == "東京"


@pytest.mark.asyncio
async def test_generate_no_city_requested_uses_llm_city():
    client, _ = make_client(llm_reply(city="清邁"))
    result = await generate({"summary": "想避寒", "preferences": {}}, client)
    assert result["fail_reason"] is None
    assert result["city"] == "清邁"


@pytest.mark.asyncio
async def test_generate_llm_exception_soft_fails():
    client, _ = make_client(RuntimeError("boom"))
    result = await generate(dict(BASE_PARAMS), client)
    assert result["fail_reason"] is not None and "boom" in result["fail_reason"]
    assert result["itinerary_patch"]["days"] == []
    assert result["ai_model"] == "guide-model"


@pytest.mark.asyncio
async def test_generate_missing_itinerary_soft_fails():
    client, _ = make_client('{"city": "京都"}')
    result = await generate(dict(BASE_PARAMS), client)
    assert result["fail_reason"] is not None and "itinerary" in result["fail_reason"]


@pytest.mark.asyncio
async def test_generate_nulls_any_hallucinated_ids():
    # 白名單永遠為空 → LLM 若自己生 oid/prod_id 一律濾掉（契約欄位仍在、值為 null）
    reply = llm_reply(
        itinerary=[{"items": [{"name": "環球影城", "type": "spot",
                               "oid": "26KK1", "prod_id": "157138"}]}],
        days=1,
    )
    client, _ = make_client(reply)
    result = await generate(dict(BASE_PARAMS), client)
    day = result["itinerary_patch"]["days"][0]
    assert day["items"][0]["oid"] is None and day["items"][0]["prod_id"] is None
    assert day["booked_anchor"] is None


@pytest.mark.asyncio
async def test_generate_day_numbering_prefers_model_day_falls_back_to_index():
    reply = llm_reply(itinerary=[{"day": 5, "items": []}, {"items": []}])
    client, _ = make_client(reply)
    result = await generate(dict(BASE_PARAMS), client)
    assert [d["day"] for d in result["itinerary_patch"]["days"]] == [5, 2]


@pytest.mark.asyncio
async def test_generate_duplicate_model_days_renumbered_by_index():
    # 線上實測（llama-3.3-70b）：模型把最後一天拆成兩個 day=5 區塊——
    # 編號不唯一時不採信模型，全部改用陣列索引重編，避免 App 出現兩個 Day 5
    reply = llm_reply(
        itinerary=[{"day": d, "items": []} for d in (1, 2, 3, 4, 5, 5)], days=5
    )
    client, _ = make_client(reply)
    result = await generate(dict(BASE_PARAMS), client)
    assert [d["day"] for d in result["itinerary_patch"]["days"]] == [1, 2, 3, 4, 5, 6]


@pytest.mark.asyncio
async def test_generate_unplanned_days_phase_plan():
    reply = llm_reply(
        unplanned_days=[2],
        itinerary=[
            {"day": 1, "items": [{"name": "清水寺", "type": "spot"}]},
            {"day": 2, "status": "unplanned", "items": []},
        ],
    )
    client, _ = make_client(reply)
    result = await generate(dict(BASE_PARAMS), client)
    assert result["phase"] == "plan"
    assert result["unplanned_days"] == [2]
    assert result["main_action"] is None
    assert result["progress_label"] == "已排 1/2 天"


@pytest.mark.asyncio
async def test_generate_converts_simplified_output_to_traditional():
    reply = llm_reply(
        messages=[{"type": "completion", "text": "行程排好了，记得带伞"}],
        itinerary=[{"items": [{"name": "清水寺", "text": "预算充足的选择", "type": "spot"}]}],
    )
    client, _ = make_client(reply)
    result = await generate(dict(BASE_PARAMS), client)
    assert "記得帶傘" in result["messages"][0]["text"]
    assert result["itinerary_patch"]["days"][0]["items"][0]["text"] == "預算充足的選擇"


@pytest.mark.asyncio
async def test_generate_date_range_partial_treated_as_none():
    client, _ = make_client(llm_reply(date_range={"start": "2026-09-01"}))
    result = await generate(dict(BASE_PARAMS), client)
    assert result["date_range"] is None

    client, _ = make_client(llm_reply(date_range={"start": "2026-09-01", "end": "2026-09-03"}))
    result = await generate(dict(BASE_PARAMS), client)
    assert result["date_range"] == {"start": "2026-09-01", "end": "2026-09-03"}


@pytest.mark.asyncio
async def test_generate_days_fallback_to_itinerary_length():
    client, _ = make_client(llm_reply(days=0))
    result = await generate(dict(BASE_PARAMS), client)
    assert result["days"] == 2


# ---------------------------------------------------------------------------
# 行程 hero 圖（2026-08-16 新增）：與 Phase 1 同風格、以行程主目的地為主體
# ---------------------------------------------------------------------------


class FakeImageProvider:
    def __init__(self):
        self.calls: list[dict] = []
        self.fail = False

    async def generate(self, prompt, *, model, width=None, height=None, steps=None):
        self.calls.append({"prompt": prompt, "model": model, "width": width, "height": height})
        if self.fail:
            raise RuntimeError("image boom")
        return b"\xff\xd8\xff\xe0img"


class TestGuideHeroPrompt:
    def test_three_layers_and_city_substituted(self):
        from app.services.plan.guide import build_guide_hero_prompt
        from app.services.companion.share_prompts import HERO_HARD_CONSTRAINTS

        prompt = build_guide_hero_prompt("京都")
        # 與 Phase 1 同風格：同一份 art_style（第 1 層）+ 同一組硬約束（第 3 層、放最後）
        assert prompt.startswith("Anime-style illustration")
        assert '"京都"' in prompt
        assert prompt.rstrip().endswith(HERO_HARD_CONSTRAINTS)


@pytest.mark.asyncio
async def test_generate_without_image_provider_returns_null_hero_image():
    client, _ = make_client(llm_reply())
    result = await generate(dict(BASE_PARAMS), client)
    assert result["hero_image_url"] is None  # 欄位存在、未接產圖時為 null


@pytest.mark.asyncio
async def test_generate_with_image_provider_returns_hero_image_url():
    from app.storage.objects import InMemoryObjectStore

    client, _ = make_client(llm_reply())
    image_provider, store = FakeImageProvider(), InMemoryObjectStore()
    result = await generate(
        dict(BASE_PARAMS), client, image_provider=image_provider, image_store=store
    )
    assert result["fail_reason"] is None
    assert result["hero_image_url"].startswith("https://")
    assert "guide-hero/" in result["hero_image_url"]
    [call] = image_provider.calls
    # 橫幅 16:9（與 Phase 1 海報 hero 的直式不同——行程頁是橫幅版位）
    assert call["width"] == 2048 and call["height"] == 1152
    assert "京都" in call["prompt"]
    assert len(store.objects) == 1


@pytest.mark.asyncio
async def test_guide_hero_image_cached_by_city_across_users():
    from app.storage.objects import InMemoryObjectStore

    client, _ = make_client(llm_reply())
    image_provider, store = FakeImageProvider(), InMemoryObjectStore()
    first = await generate(
        dict(BASE_PARAMS), client, image_provider=image_provider, image_store=store
    )
    client2, _ = make_client(llm_reply())
    second = await generate(
        dict(BASE_PARAMS), client2, image_provider=image_provider, image_store=store
    )
    assert len(image_provider.calls) == 1  # 同城市跨請求共用快取，不重產
    assert second["hero_image_url"] == first["hero_image_url"]


@pytest.mark.asyncio
async def test_guide_hero_image_failure_soft_degrades():
    from app.storage.objects import InMemoryObjectStore

    client, _ = make_client(llm_reply())
    image_provider = FakeImageProvider()
    image_provider.fail = True
    result = await generate(
        dict(BASE_PARAMS), client, image_provider=image_provider, image_store=InMemoryObjectStore()
    )
    assert result["fail_reason"] is None  # 產圖失敗不影響行程本體
    assert result["hero_image_url"] is None
    # 行程內容照常
    assert result["city"] == "京都" and result["days"] == 2


@pytest.mark.asyncio
async def test_guide_hero_image_skipped_on_llm_failure():
    from app.storage.objects import InMemoryObjectStore

    client, _ = make_client(RuntimeError("boom"))
    image_provider = FakeImageProvider()
    result = await generate(
        dict(BASE_PARAMS), client, image_provider=image_provider, image_store=InMemoryObjectStore()
    )
    assert result["fail_reason"] is not None
    assert result["hero_image_url"] is None
    assert image_provider.calls == []  # 行程失敗就不花產圖成本
