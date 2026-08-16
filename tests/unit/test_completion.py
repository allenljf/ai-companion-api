"""quiz-completions 服務測試（source-spec 4.3，mock LLM）。

重點：travel_identity 一律以 mapping 結果覆寫 LLM 輸出；成功才快取 24h；
companion_quote ≤30 字、recommendation 每段 ≤200 字（句尾截斷保險）。
"""

import json

import pytest

from app.services.companion.completion import (
    CACHE_KEY_PREFIX,
    build_completion_user_message,
    complete_quiz,
)
from app.services.llm.client import LLMClient, LLMProvider
from app.storage.kv import InMemoryKV

UUID = "b0e7a3a8-8f2f-4c1e-9d2f-1c9a35c1d001"

BASE_PARAMS = {
    "completion_uuid": UUID,
    "personality": "humorous",
    "speech_style": "friendly",
    "selected_tags": ["t1-1", "t2-3", "t5-3", "t7-3", "t8-3"],  # → D2 遠征冒險團
    "shown_cities": ["東京"],
    "companion_name": "阿旅",
    "partner_avatar_url": "https://x/avatar.png",
}


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
    return LLMClient(
        providers={"stub": provider}, routing={"quiz_completions": ("stub", "completion-model")}
    ), provider


def llm_reply(**overrides) -> str:
    body = {
        "travel_identity": "LLM亂給的稱號",
        "travel_identity_en": "Wrong Title",
        "destination_cn": "清邁",
        "destination_en": "Chiang Mai",
        "destination_country": "泰國",
        "destination_country_en": "Thailand",
        "tagline": "冒險就要往山裡去",
        "tagline_en": "Into the wild",
        "highlight_tags": ["資深吃貨", "戶外挑戰咖", "越冷門越想衝"],
        "highlight_tags_en": ["Foodie", "Outdoor Challenger", "Off-the-beaten-path"],
        "companion_quote": "出發吧，冒險在等你！",
        "companion_quote_en": "Let's go, adventure awaits!",
        "reasoning": ["嗯，你不喜歡人擠人…", "熱鬧的夜市倒是可以", "啊，有了！", "清邁，山城的冒險基地"],
        "recommendation": ["第一段推薦內容。", "第二段推薦內容。", "第三段推薦內容。"],
        "social_post": "去清邁！#KKday",
    } | overrides
    return json.dumps(body, ensure_ascii=False)


@pytest.mark.asyncio
async def test_success_envelope_and_identity_override():
    client, provider = make_client(llm_reply())
    kv = InMemoryKV()
    result = await complete_quiz(dict(BASE_PARAMS), client, kv)

    assert result["fail_reason"] is None
    # 稱號一律以 mapping 覆寫，不採 LLM 輸出
    assert result["travel_identity"] == "遠征冒險團"
    assert result["travel_identity_en"] == "Expedition Adventure Crew"
    assert result["destination_cn"] == "清邁"
    assert result["highlight_tags"] == ["資深吃貨", "戶外挑戰咖", "越冷門越想衝"]
    assert result["recommendation"] == ["第一段推薦內容。", "第二段推薦內容。", "第三段推薦內容。"]
    assert result["reasoning"][0] == "嗯，你不喜歡人擠人…"
    assert result["share_image_status"] == "pending"
    assert result["ai_model"] == "completion-model"
    assert isinstance(result["quiz_completion_id"], int)
    assert result["share_image_size"] == "1152x2048"
    assert result["share_image_quality"] == "low"
    assert result["art_style_version"] == "2"
    assert provider.calls[0]["max_tokens"] == 3000

    # prompt 檢查：mapping 出的稱號要進 user message（LLM 只能沿用）
    user = provider.calls[0]["user"]
    assert "遠征冒險團" in user
    # tag id 解析為 label 才進 prompt（識別碼絕不進 prompt 的變形：人類可讀文字）
    assert "資深吃貨" in user and "t1-1" not in user
    assert "幽默風趣" in user and "好朋友" in user


@pytest.mark.asyncio
async def test_success_caches_analysis_permanently():
    # 2026-08-16 需求變更：分析快取不設 TTL（永久保留，清除交給使用者手動 DB 操作）
    client, _ = make_client(llm_reply())
    kv = InMemoryKV()
    await complete_quiz(dict(BASE_PARAMS), client, kv)
    assert (CACHE_KEY_PREFIX + UUID) in kv._store
    expires_at, _ = kv._store[CACHE_KEY_PREFIX + UUID]
    assert expires_at == float("inf")
    cached = await kv.get(CACHE_KEY_PREFIX + UUID)
    assert cached is not None
    assert cached["analysis"]["travel_identity"] == "遠征冒險團"  # 覆寫後才快取
    assert cached["companion_name"] == "阿旅"
    assert cached["partner_avatar_url"] == "https://x/avatar.png"


@pytest.mark.asyncio
async def test_failure_does_not_cache_and_skips_image():
    client, _ = make_client(RuntimeError("boom"))
    kv = InMemoryKV()
    result = await complete_quiz(dict(BASE_PARAMS), client, kv)
    assert result["fail_reason"] is not None and "boom" in result["fail_reason"]
    assert result["share_image_status"] == "skipped"
    # 失敗仍回 mapping 稱號（規則式判定不依賴 LLM）
    assert result["travel_identity"] == "遠征冒險團"
    assert await kv.get(CACHE_KEY_PREFIX + UUID) is None


@pytest.mark.asyncio
async def test_unparseable_reply_is_soft_failure():
    client, _ = make_client("我不會回 JSON")
    result = await complete_quiz(dict(BASE_PARAMS), client, InMemoryKV())
    assert result["fail_reason"] is not None
    assert result["travel_identity"] == "遠征冒險團"
    assert result["destination_cn"] == ""


@pytest.mark.asyncio
async def test_quote_truncated_at_30_and_converted():
    long_quote = "出发吧！" + "后面这段会超过三十个字所以要被截断" * 3
    client, _ = make_client(llm_reply(companion_quote=long_quote))
    result = await complete_quiz(dict(BASE_PARAMS), client, InMemoryKV())
    assert len(result["companion_quote"]) <= 30
    assert result["companion_quote"].startswith("出發吧！")


@pytest.mark.asyncio
async def test_recommendation_string_compat_and_truncation():
    # 相容舊版 prompt 回單一字串 → 包成單元素陣列；每段 ≤200 截斷
    client, _ = make_client(llm_reply(recommendation="就一段。" + "很長的推薦" * 60))
    result = await complete_quiz(dict(BASE_PARAMS), client, InMemoryKV())
    assert len(result["recommendation"]) == 1
    assert len(result["recommendation"][0]) <= 200


@pytest.mark.asyncio
async def test_reasoning_cleanup():
    client, _ = make_client(llm_reply(reasoning=["  第一句  ", "", None, "第二句"]))
    result = await complete_quiz(dict(BASE_PARAMS), client, InMemoryKV())
    assert result["reasoning"] == ["第一句", "第二句"]


@pytest.mark.asyncio
async def test_highlight_tags_sliced_to_three():
    client, _ = make_client(llm_reply(highlight_tags=["a", "b", "c", "d", "e"]))
    result = await complete_quiz(dict(BASE_PARAMS), client, InMemoryKV())
    assert result["highlight_tags"] == ["a", "b", "c"]


@pytest.mark.asyncio
async def test_simplified_output_converted_to_traditional():
    client, _ = make_client(
        llm_reply(destination_cn="冲绳", social_post="记得带伞去冲绳")
    )
    result = await complete_quiz(dict(BASE_PARAMS), client, InMemoryKV())
    assert result["destination_cn"] == "沖繩"
    assert "記得帶傘" in result["social_post"]


@pytest.mark.asyncio
async def test_cache_write_failure_does_not_fail_request():
    class BrokenKV(InMemoryKV):
        async def set(self, *args, **kwargs):
            raise RuntimeError("kv down")

    client, _ = make_client(llm_reply())
    result = await complete_quiz(dict(BASE_PARAMS), client, BrokenKV())
    assert result["fail_reason"] is None


def test_user_message_shape():
    identity = {"travel_identity": "遠征冒險團", "travel_identity_en": "Expedition Adventure Crew"}
    message = build_completion_user_message(dict(BASE_PARAMS), identity)
    payload = json.loads(message[len("<<<USER_INPUT\n") : -len("\n>>>END_USER_INPUT")])
    assert payload["shown_cities"] == ["東京"]
    assert payload["companion_name"] == "阿旅"
    assert payload["travel_identity"] == "遠征冒險團"
    assert payload["selected_tags"][0] == "資深吃貨"
