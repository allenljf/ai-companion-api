"""quiz 服務測試（source-spec 4.2：加權隨機選題 + LLM 只改寫語氣，mock LLM）。"""

import json
import random
from pathlib import Path

import pytest

from app.services.llm.client import LLMClient, LLMProvider
from app.services.companion.quiz import (
    fetch_quiz,
    merge_rewritten_text,
    select_one_per_dimension,
)

DIMENSIONS = json.loads(Path("data/quiz_dimensions.json").read_text())["dimensions"]


# ---------------------------------------------------------------------------
# 加權隨機選題（不經 LLM）
# ---------------------------------------------------------------------------


class TestSelectOnePerDimension:
    def test_selects_exactly_one_per_dimension(self):
        selected = select_one_per_dimension({}, DIMENSIONS, random.Random(1))
        assert len(selected) == 8
        assert [q["dimension_id"] for q in selected] == [d["id"] for d in DIMENSIONS]

    def test_options_carry_tag_object(self):
        selected = select_one_per_dimension({}, DIMENSIONS, random.Random(1))
        for question in selected:
            for option in question["options"]:
                assert option["tag"]["id"] == option["tag_id"]
                assert option["tag"]["label"]

    def test_weight_halves_per_shown_count(self):
        # 維度 7 有 4 題；把其他 3 題各標記出現 30 次（權重 ~0），必然選中沒出現過的那題
        dim7 = next(d for d in DIMENSIONS if d["id"] == 7)
        counts = {q["id"]: 30 for q in dim7["quizzes"] if q["id"] != "7-2"}
        for seed in range(20):
            [selected] = select_one_per_dimension(counts, [dim7], random.Random(seed))
            assert selected["id"] == "7-2"

    def test_uniform_when_no_counts(self):
        # 沒有出現紀錄時每題都有機會被抽中（跑多個 seed 應覆蓋多題）
        dim7 = next(d for d in DIMENSIONS if d["id"] == 7)
        picked = {
            select_one_per_dimension({}, [dim7], random.Random(seed))[0]["id"]
            for seed in range(50)
        }
        assert len(picked) >= 3

    def test_empty_dimensions_raises(self):
        with pytest.raises(Exception):
            select_one_per_dimension({}, [], random.Random(1))

    def test_does_not_mutate_source_dimensions(self):
        before = json.dumps(DIMENSIONS, ensure_ascii=False, sort_keys=True)
        select_one_per_dimension({"1-1": 2}, DIMENSIONS, random.Random(1))
        assert json.dumps(DIMENSIONS, ensure_ascii=False, sort_keys=True) == before


# ---------------------------------------------------------------------------
# 合併改寫結果：只取 text，id/index/其他欄位一律用原始值
# ---------------------------------------------------------------------------


def question(qid: str, text: str, options: list[tuple[int, str]]) -> dict:
    return {
        "id": qid,
        "text": text,
        "mode": "Character",
        "type": "生活情境",
        "index": 1,
        "dimension_id": 1,
        "options": [
            {"id": f"{qid}-{i}", "text": t, "index": i, "tag_id": f"t{i}",
             "image_url": f"https://x/{qid}-{i}.jpg"}
            for i, t in options
        ],
    }


class TestMergeRewrittenText:
    def test_merges_only_text_fields(self):
        originals = [question("1-1", "原題目", [(1, "原選項一"), (2, "原選項二")])]
        rewritten = [
            {"id": "1-1", "text": "改寫題目", "options": [
                {"index": 1, "text": "改寫選項一"}, {"index": 2, "text": "改寫選項二"},
            ]}
        ]
        [merged] = merge_rewritten_text(originals, rewritten)
        assert merged["text"] == "改寫題目"
        assert merged["options"][0]["text"] == "改寫選項一"
        # 其他欄位一律原始值
        assert merged["options"][0]["image_url"] == "https://x/1-1-1.jpg"
        assert merged["options"][0]["tag_id"] == "t1"
        assert merged["id"] == "1-1" and merged["index"] == 1

    def test_unknown_question_id_keeps_original(self):
        originals = [question("1-1", "原題目", [(1, "原選項")])]
        [merged] = merge_rewritten_text(originals, [{"id": "9-9", "text": "亂改", "options": []}])
        assert merged["text"] == "原題目"

    def test_missing_option_index_keeps_original_option(self):
        originals = [question("1-1", "原題目", [(1, "原一"), (2, "原二")])]
        rewritten = [{"id": "1-1", "text": "新題目", "options": [{"index": 2, "text": "新二"}]}]
        [merged] = merge_rewritten_text(originals, rewritten)
        assert merged["options"][0]["text"] == "原一"
        assert merged["options"][1]["text"] == "新二"

    def test_simplified_chinese_rewrites_converted_to_traditional(self):
        originals = [question("1-1", "原題目", [(1, "原選項")])]
        rewritten = [{"id": "1-1", "text": "预算内的选择", "options": [{"index": 1, "text": "读万卷书"}]}]
        [merged] = merge_rewritten_text(originals, rewritten)
        assert merged["text"] == "預算內的選擇"
        assert merged["options"][0]["text"] == "讀萬卷書"


# ---------------------------------------------------------------------------
# fetch_quiz 端到端（mock LLM）
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
    return LLMClient(
        providers={"stub": provider}, routing={"quiz": ("stub", "quiz-model")}
    ), provider


def rewrite_reply_for(questions_user_message: str) -> str:
    """依 user message 內的題目組出全部改寫的回覆。"""
    payload = json.loads(
        questions_user_message[len("<<<USER_INPUT\n") : -len("\n>>>END_USER_INPUT")]
    )
    return json.dumps(
        {
            "questions": [
                {
                    "id": q["id"],
                    "text": f"改寫版{q['id']}",
                    "options": [
                        {"index": o["index"], "text": f"改寫選項{q['id']}-{o['index']}"}
                        for o in q["options"]
                    ],
                }
                for q in payload["questions"]
            ]
        },
        ensure_ascii=False,
    )


@pytest.mark.asyncio
async def test_fetch_quiz_success_rewrites_and_strips_tag_id():
    # 兩段式 stub：先抽題（rng 固定），再依抽中的題目回改寫
    client, provider = make_client("{}")

    async def chat_with_dynamic_reply(system, user, **kwargs):
        provider.calls.append({"system": system, "user": user})
        return rewrite_reply_for(user)

    provider.chat = chat_with_dynamic_reply
    result = await fetch_quiz(
        {"shown_question_counts": {}, "personality": "humorous", "speech_style": "friendly"},
        client,
        rng=random.Random(1),
    )
    assert result["fail_reason"] is None
    assert result["count"] == 8
    for q in result["questions"]:
        assert q["text"].startswith("改寫版")
        for o in q["options"]:
            assert "tag_id" not in o        # 回應剝除 tag_id
            assert o["tag"]["label"]        # 但保留 tag 物件
            assert o["image_url"].startswith("https://")

    system = provider.calls[0]["system"]
    # persona label+description 進 prompt（tag 解析為人類可讀文字）
    assert "幽默風趣" in system
    assert "好朋友" in system


@pytest.mark.asyncio
async def test_fetch_quiz_llm_failure_falls_back_to_original():
    client, _ = make_client(RuntimeError("boom"))
    result = await fetch_quiz(
        {"shown_question_counts": {}, "personality": "humorous", "speech_style": "friendly"},
        client,
        rng=random.Random(1),
    )
    assert result["fail_reason"] is not None
    assert result["count"] == 8
    # fallback 原始題目仍完整可用
    for q in result["questions"]:
        assert q["text"] and q["options"]
        assert all("tag_id" not in o and o.get("tag") for o in q["options"])


@pytest.mark.asyncio
async def test_fetch_quiz_bad_json_falls_back():
    client, _ = make_client('{"nope": true}')
    result = await fetch_quiz(
        {"shown_question_counts": {}, "personality": "humorous", "speech_style": "friendly"},
        client,
        rng=random.Random(1),
    )
    assert result["fail_reason"] is not None
    assert result["count"] == 8


@pytest.mark.asyncio
async def test_fetch_quiz_unknown_persona_tag_passthrough():
    # 不在 ai_partner 清單的 tag：label fallback 為 tag 本身（PHP ?? 容錯）
    client, provider = make_client('{"nope": true}')
    result = await fetch_quiz(
        {"shown_question_counts": {}, "personality": "made_up", "speech_style": "custom"},
        client,
        rng=random.Random(1),
    )
    assert result["count"] == 8
    assert "made_up" in provider.calls[0]["system"]
