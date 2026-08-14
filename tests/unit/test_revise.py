"""travel-revise service 測試（source-spec 5.7 / 8.11，mock LLM）。

與 guide 的關鍵差異（都要有測試）：
- day 一律依陣列順序重編 1..N（忽略輸入/模型的 day 欄位）
- changed_days 後端逐位置 diff，不採信 LLM 自報
- 離題/失敗一律原樣返回「正規化過的輸入行程」（days 不會是空的）；離題不是失敗
- 白名單 = 行程既有 id ∪ 本次帶入 id（oid/prod_id 各自獨立）
- 條件注入段依「白名單有值」決定（不是 request 有沒有帶 orders/products）
"""

import json

import pytest

from app.core.prompt_loader import render_prompt
from app.services.llm.client import LLMClient, LLMProvider
from app.services.plan.revise import (
    build_system_prompt,
    build_user_message,
    collect_itinerary_oids,
    collect_itinerary_product_ids,
    diff_changed_days,
    normalize_sequential_days,
    revise,
)


def make_day(*items: dict, **overrides) -> dict:
    return {"status": "planned", "kind": "normal", "half_day": False, "items": list(items)} | overrides


SPOT = {"name": "清水寺", "text": "清晨人少", "type": "spot", "time": "09:00",
        "time_band": "上午", "note": None, "lat": 34.99, "lng": 135.78}
MEAL = {"name": "一蘭拉麵", "text": "宵夜場", "type": "meal", "time": "21:00", "time_band": "晚上"}

BASE_MESSAGES = [
    {"role": "user", "content": "幫我排京都三天"},
    {"role": "assistant", "content": "排好了！"},
    {"role": "user", "content": "第二天加一個抹茶體驗"},
]

BASE_PARAMS = {
    "itinerary": [make_day(SPOT), make_day(MEAL)],
    "city": "京都",
    "messages": BASE_MESSAGES,
    "preferences": {"pace": "輕鬆"},
}


# ---------------------------------------------------------------------------
# 白名單收集：行程既有 id（非 scalar 防炸）
# ---------------------------------------------------------------------------


class TestCollectItineraryIds:
    def test_collects_unique_oids_in_order(self):
        days = [
            make_day({"oid": "O1"}, {"oid": " O2 "}),
            make_day({"oid": "O1"}, {"name": "x"}),
        ]
        assert collect_itinerary_oids(days) == ["O1", "O2"]

    def test_non_scalar_and_empty_skipped_without_crash(self):
        days = [make_day({"oid": ["A"]}, {"oid": {"x": 1}}, {"oid": ""}, {"oid": True})]
        assert collect_itinerary_oids(days) == []

    def test_collects_prod_ids_independently(self):
        days = [make_day({"prod_id": "P1", "oid": "O1"}, {"prod_id": "P1"})]
        assert collect_itinerary_product_ids(days) == ["P1"]

    def test_tolerates_malformed_days(self):
        # itinerary.* 刻意不深驗：day 不是 dict、items 不是 list 都不能炸
        assert collect_itinerary_oids(["oops", {"items": "bad"}, {}]) == []
        assert collect_itinerary_product_ids([None, {"items": [None, "x"]}]) == []

    def test_oid_zero_kept(self):
        assert collect_itinerary_oids([make_day({"oid": "0"})]) == ["0"]


# ---------------------------------------------------------------------------
# sequential 重編：一律 1..N，忽略任何既有 day 欄位
# ---------------------------------------------------------------------------


class TestNormalizeSequentialDays:
    def test_renumbers_by_array_order_ignoring_day_fields(self):
        days = normalize_sequential_days(
            [make_day(day=7), make_day(day=1), make_day(day=99)], [], []
        )
        assert [d["day"] for d in days] == [1, 2, 3]

    def test_applies_whitelist_and_dedupe(self):
        days = normalize_sequential_days(
            [make_day({"oid": "O1", "type": "spot"}), make_day({"oid": "O1", "type": "spot"})],
            ["O1"],
            [],
        )
        assert days[0]["items"][0]["oid"] == "O1"
        assert days[1]["items"][0]["oid"] is None
        assert days[0]["booked_anchor"] == {"oids": ["O1"]}
        assert days[1]["booked_anchor"] is None

    def test_empty_input_gives_empty_list(self):
        assert normalize_sequential_days([], [], []) == []


# ---------------------------------------------------------------------------
# changed_days：後端逐位置 diff
# ---------------------------------------------------------------------------


class TestDiffChangedDays:
    def _norm(self, days):
        return normalize_sequential_days(days, ["O1"], [])

    def test_no_change_empty(self):
        original = self._norm([make_day(SPOT), make_day(MEAL)])
        revised = self._norm([make_day(SPOT), make_day(MEAL)])
        assert diff_changed_days(original, revised) == []

    def test_item_content_change_detected(self):
        original = self._norm([make_day(SPOT), make_day(MEAL)])
        revised = self._norm([make_day(SPOT), make_day(MEAL | {"time": "20:00"})])
        assert diff_changed_days(original, revised) == [2]

    def test_status_kind_half_day_changes_detected(self):
        original = self._norm([make_day(SPOT)])
        assert diff_changed_days(original, self._norm([make_day(SPOT, status="unplanned")])) == [1]
        assert diff_changed_days(original, self._norm([make_day(SPOT, kind="departure")])) == [1]
        assert diff_changed_days(original, self._norm([make_day(SPOT, half_day=True)])) == [1]

    def test_inserted_day_shifts_positions(self):
        original = self._norm([make_day(SPOT), make_day(MEAL)])
        new_day = make_day({"name": "抹茶體驗", "type": "spot", "time": "14:00"})
        revised = self._norm([make_day(SPOT), new_day, make_day(MEAL)])
        # 位置 2 換成新的一天、位置 3 是位移後的原 Day 2 → 都算變動
        assert diff_changed_days(original, revised) == [2, 3]

    def test_extra_days_beyond_original_are_changed(self):
        original = self._norm([make_day(SPOT)])
        revised = self._norm([make_day(SPOT), make_day(MEAL)])
        assert diff_changed_days(original, revised) == [2]

    def test_deleting_trailing_day_yields_empty_diff(self):
        # 對照 PHP 行為：只迭代 revised 的位置，刪掉最後一天 → changed_days 為空。
        # changed_days 純粹是 UI 高亮提示，App 整包重渲染仍正確
        original = self._norm([make_day(SPOT), make_day(MEAL)])
        revised = self._norm([make_day(SPOT)])
        assert diff_changed_days(original, revised) == []


# ---------------------------------------------------------------------------
# prompt 組裝
# ---------------------------------------------------------------------------


class TestBuildSystemPrompt:
    def test_base_prompt_no_conditional_sections(self):
        prompt = build_system_prompt(BASE_PARAMS, [], [])
        assert "你是 KKday「AI 旅伴」的行程修改引擎。" in prompt
        assert "## 本次優先修改範圍" not in prompt
        assert "## 已預訂項目（不可刪除）" not in prompt
        assert "## 使用者挑選的商品（非必要不要刪除）" not in prompt
        assert "## 輸出格式（嚴格遵守）" in prompt

    def test_target_day_section_injected(self):
        prompt = build_system_prompt(BASE_PARAMS | {"target_day": 2}, [], [])
        assert "## 本次優先修改範圍" in prompt
        assert "使用者從 Day 2 進入" in prompt
        assert "把 Day 2 的午餐挪到明天" in prompt

    def test_sections_injected_by_whitelist_not_request_params(self):
        # 注入條件是白名單有值（行程既有 oid 也算），不是 request 有沒有帶 orders
        prompt = build_system_prompt(BASE_PARAMS, ["O1"], [])
        assert "## 已預訂項目（不可刪除）" in prompt
        assert "## 使用者挑選的商品" not in prompt

        prompt = build_system_prompt(BASE_PARAMS, [], ["P1"])
        assert "## 已預訂項目" not in prompt
        assert "## 使用者挑選的商品（非必要不要刪除）" in prompt

    def test_strength_difference_between_sections(self):
        prompt = build_system_prompt(BASE_PARAMS, ["O1"], ["P1"])
        # oid：不可刪 + 拒絕並引導；prod_id：明確要求可刪
        assert "不要刪除，在 reply 用你的語氣說明這是已預訂的行程" in prompt
        assert "使用者明確說要刪掉某個挑選的商品時，可以照做" in prompt
        assert prompt.index("## 已預訂項目") < prompt.index("## 使用者挑選的商品")

    def test_no_sections_prompt_identical_to_base_template(self):
        from app.services.plan.persona import persona_text

        expected = render_prompt(
            "phase2/revise",
            persona=persona_text(BASE_PARAMS),
            target_day=None,
            booked_orders_section="",
            selected_products_section="",
        )
        assert build_system_prompt(BASE_PARAMS, [], []) == expected


class TestBuildUserMessage:
    def _payload(self, params: dict) -> dict:
        msg = build_user_message(params)
        return json.loads(msg[len("<<<USER_INPUT\n") : -len("\n>>>END_USER_INPUT")])

    def test_base_payload(self):
        payload = self._payload(BASE_PARAMS)
        assert payload["itinerary"] == BASE_PARAMS["itinerary"]
        assert payload["target_day"] is None
        assert payload["city"] == "京都"
        assert payload["conversation"] == BASE_MESSAGES
        assert "orders" not in payload and "products" not in payload

    def test_orders_and_products_payload(self):
        payload = self._payload(
            BASE_PARAMS
            | {
                "target_day": 2,
                "orders": [{"oid": "O1", "prod_name": "門票"}],
                "products": [{"prod_id": "P1", "prod_name": "導覽"}],
            }
        )
        assert payload["target_day"] == 2
        assert payload["orders"][0]["oid"] == "O1"
        assert payload["products"][0]["prod_id"] == "P1"


# ---------------------------------------------------------------------------
# revise：端到端（mock LLM）
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
        providers={"stub": provider}, routing={"travel_revise": ("stub", "revise-model")}
    )
    return client, provider


def llm_reply(**overrides) -> str:
    body = {
        "city": "京都",
        "reply": "第二天加了抹茶體驗！",
        "off_topic": False,
        "changed_summary": "- Day 2 下午加了抹茶體驗",
        "unplanned_days": [],
        "pending_fields": [],
        "itinerary": [
            make_day(SPOT),
            make_day(MEAL, {"name": "抹茶體驗", "text": "宇治抹茶手作", "type": "spot",
                            "time": "14:00", "time_band": "下午", "lat": 34.9, "lng": 135.8}),
        ],
    } | overrides
    return json.dumps(body, ensure_ascii=False)


@pytest.mark.asyncio
async def test_revise_success_envelope_and_diff():
    client, provider = make_client(llm_reply())
    result = await revise(dict(BASE_PARAMS), client)

    assert result["fail_reason"] is None
    assert result["off_topic"] is False
    assert result["city"] == "京都"
    assert result["reply"] == "第二天加了抹茶體驗！"
    assert result["changed_summary"] == "- Day 2 下午加了抹茶體驗"
    assert result["itinerary_patch"]["mode"] == "full"
    assert [d["day"] for d in result["itinerary_patch"]["days"]] == [1, 2]
    # Day 1 原樣、Day 2 多了 item → 只有 2 變動
    assert result["itinerary_patch"]["changed_days"] == [2]
    assert result["days"] == 2
    assert result["phase"] == "done"
    assert result["main_action"] == {"type": "view_trip", "label": "看看完整行程"}
    assert result["ai_model"] == "revise-model"
    assert provider.calls[0]["max_tokens"] == 8000


@pytest.mark.asyncio
async def test_revise_llm_failure_returns_original_itinerary():
    client, _ = make_client(RuntimeError("boom"))
    result = await revise(dict(BASE_PARAMS), client)
    assert result["fail_reason"] is not None and "boom" in result["fail_reason"]
    assert result["off_topic"] is False
    # 原樣返回：days 是正規化過的輸入行程，不會是空的
    days = result["itinerary_patch"]["days"]
    assert [d["day"] for d in days] == [1, 2]
    assert days[0]["items"][0]["name"] == "清水寺"
    assert result["itinerary_patch"]["changed_days"] == []
    assert result["reply"] == "嗯…我這邊卡了一下，再說一次剛剛那句好嗎？"
    assert result["city"] is None


@pytest.mark.asyncio
async def test_revise_off_topic_returns_original_not_failure():
    reply = llm_reply(
        off_topic=True,
        reply="我們來繼續調整行程吧！",
        itinerary=[{"status": "planned", "items": [{"name": "幻覺景點", "type": "spot"}]}],
    )
    client, _ = make_client(reply)
    result = await revise(dict(BASE_PARAMS), client)
    # 離題不是失敗；不採信 LLM 的 itinerary，原樣返回輸入行程
    assert result["fail_reason"] is None
    assert result["off_topic"] is True
    assert result["reply"] == "我們來繼續調整行程吧！"
    days = result["itinerary_patch"]["days"]
    assert days[0]["items"][0]["name"] == "清水寺"
    assert all("幻覺景點" != i.get("name") for d in days for i in d["items"])
    assert result["itinerary_patch"]["changed_days"] == []


@pytest.mark.asyncio
async def test_revise_missing_reply_soft_fails_with_original():
    client, _ = make_client('{"itinerary": []}')
    result = await revise(dict(BASE_PARAMS), client)
    assert result["fail_reason"] is not None and "reply" in result["fail_reason"]
    assert [d["day"] for d in result["itinerary_patch"]["days"]] == [1, 2]


@pytest.mark.asyncio
async def test_revise_missing_itinerary_soft_fails_with_original():
    client, _ = make_client('{"reply": "好的"}')
    result = await revise(dict(BASE_PARAMS), client)
    assert result["fail_reason"] is not None and "itinerary" in result["fail_reason"]
    assert [d["day"] for d in result["itinerary_patch"]["days"]] == [1, 2]


@pytest.mark.asyncio
async def test_revise_city_mismatch_soft_fails_with_original():
    client, _ = make_client(llm_reply(city="大阪"))
    result = await revise(dict(BASE_PARAMS), client)
    assert result["fail_reason"] is not None
    assert "大阪" in result["fail_reason"] and "京都" in result["fail_reason"]
    assert [d["day"] for d in result["itinerary_patch"]["days"]] == [1, 2]


@pytest.mark.asyncio
async def test_revise_simplified_city_not_false_failure():
    client, _ = make_client(llm_reply(city="东京"))
    result = await revise(dict(BASE_PARAMS) | {"city": "東京"}, client)
    assert result["fail_reason"] is None
    assert result["city"] == "東京"


@pytest.mark.asyncio
async def test_revise_whitelist_union_keeps_existing_ids_without_rebringing():
    # 行程既有 oid/prod_id，App 沒重帶 orders/products → id 不能被洗成 null
    params = dict(BASE_PARAMS) | {
        "itinerary": [make_day(SPOT | {"oid": "O1"}), make_day(MEAL | {"prod_id": "P1"})]
    }
    reply = llm_reply(
        itinerary=[
            make_day(SPOT | {"oid": "O1"}),
            make_day(MEAL | {"prod_id": "P1"}),
        ]
    )
    client, _ = make_client(reply)
    result = await revise(params, client)
    days = result["itinerary_patch"]["days"]
    assert days[0]["items"][0]["oid"] == "O1"
    assert days[0]["booked_anchor"] == {"oids": ["O1"]}
    assert days[1]["items"][0]["prod_id"] == "P1"
    assert result["itinerary_patch"]["changed_days"] == []


@pytest.mark.asyncio
async def test_revise_whitelist_union_includes_new_orders():
    params = dict(BASE_PARAMS) | {"orders": [{"oid": "NEW1", "prod_name": "門票"}]}
    reply = llm_reply(
        itinerary=[make_day(SPOT | {"oid": "NEW1"}), make_day(MEAL)]
    )
    client, _ = make_client(reply)
    result = await revise(params, client)
    assert result["itinerary_patch"]["days"][0]["items"][0]["oid"] == "NEW1"


@pytest.mark.asyncio
async def test_revise_hallucinated_ids_filtered():
    reply = llm_reply(itinerary=[make_day(SPOT | {"oid": "GHOST", "prod_id": "GHOST"})])
    client, _ = make_client(reply)
    result = await revise(dict(BASE_PARAMS), client)
    item = result["itinerary_patch"]["days"][0]["items"][0]
    assert item["oid"] is None and item["prod_id"] is None


@pytest.mark.asyncio
async def test_revise_deleted_day_renumbers():
    reply = llm_reply(itinerary=[make_day(MEAL)], changed_summary="- 刪掉第一天")
    client, _ = make_client(reply)
    result = await revise(dict(BASE_PARAMS), client)
    days = result["itinerary_patch"]["days"]
    assert [d["day"] for d in days] == [1]
    assert days[0]["items"][0]["name"] == "一蘭拉麵"


@pytest.mark.asyncio
async def test_revise_reply_truncated_at_60_and_converted_to_traditional():
    long_reply = "改好了，记得带伞。" + "然後我又囉唆了一大段完全不會停下來的補充說明" * 5
    client, _ = make_client(llm_reply(reply=long_reply))
    result = await revise(dict(BASE_PARAMS), client)
    assert len(result["reply"]) <= 60
    assert "記得帶傘" in result["reply"]


@pytest.mark.asyncio
async def test_revise_items_text_converted_to_traditional():
    reply = llm_reply(
        itinerary=[make_day(SPOT | {"text": "预算充足的选择"}), make_day(MEAL)]
    )
    client, _ = make_client(reply)
    result = await revise(dict(BASE_PARAMS), client)
    assert result["itinerary_patch"]["days"][0]["items"][0]["text"] == "預算充足的選擇"


@pytest.mark.asyncio
async def test_revise_empty_changed_summary_becomes_none():
    client, _ = make_client(llm_reply(changed_summary="  "))
    result = await revise(dict(BASE_PARAMS), client)
    assert result["changed_summary"] is None


@pytest.mark.asyncio
async def test_revise_unplanned_days_phase_plan():
    reply = llm_reply(
        unplanned_days=[2],
        itinerary=[make_day(SPOT), make_day(status="unplanned")],
    )
    client, _ = make_client(reply)
    result = await revise(dict(BASE_PARAMS), client)
    assert result["phase"] == "plan"
    assert result["main_action"] is None
    assert result["progress_label"] == "已排 1/2 天"
