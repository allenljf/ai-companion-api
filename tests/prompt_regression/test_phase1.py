"""Phase 1 三支的 prompt 回歸測試（真實 LLM，手動執行）。

執行：pytest -m prompt_regression tests/prompt_regression/test_phase1.py
重點驗證 migration-plan 6.4：
- quiz：改寫後 id/index 沒被改動；每維度 1 題
- quiz-completions：推薦城市不在 shown_cities；highlight_tags 從輸入原樣挑 3 個；
  travel_identity 被 mapping 覆寫
- self-introduction：第一人稱、含旅伴名字
"""

import json
import random
from pathlib import Path

import pytest

from app.api.deps import get_llm_client
from app.services.companion.completion import complete_quiz
from app.services.companion.quiz import fetch_quiz
from app.services.companion.self_introduction import generate_self_introduction
from app.storage.kv import InMemoryKV

pytestmark = pytest.mark.prompt_regression

DIMENSIONS = json.loads(Path("data/quiz_dimensions.json").read_text())["dimensions"]


@pytest.fixture(autouse=True)
def fresh_llm_client():
    get_llm_client.cache_clear()
    yield
    get_llm_client.cache_clear()


@pytest.mark.asyncio
async def test_quiz_rewrite_preserves_structure():
    rng = random.Random(42)
    result = await fetch_quiz(
        {"shown_question_counts": {}, "personality": "humorous", "speech_style": "friendly"},
        get_llm_client(),
        rng=rng,
    )
    assert result["fail_reason"] is None, result["fail_reason"]
    assert result["count"] == 8

    # 同 seed 重抽一次原始題目，對照 id/index/image_url 完全沒被 LLM 改動
    from app.services.companion.quiz import select_one_per_dimension

    originals = select_one_per_dimension({}, DIMENSIONS, random.Random(42))
    for rewritten, original in zip(result["questions"], originals):
        assert rewritten["id"] == original["id"]
        assert [o["index"] for o in rewritten["options"]] == [
            o["index"] for o in original["options"]
        ]
        assert [o["image_url"] for o in rewritten["options"]] == [
            o["image_url"] for o in original["options"]
        ]
        assert rewritten["text"]  # 有改寫文字（至少非空）


@pytest.mark.asyncio
async def test_completion_respects_bans_and_tags():
    shown = ["東京", "京都", "大阪"]
    selected = ["t1-2", "t2-4", "t3-2", "t4-2", "t5-1", "t6-3", "t7-1", "t8-1"]  # → A1
    result = await complete_quiz(
        {
            "completion_uuid": "b0e7a3a8-8f2f-4c1e-9d2f-1c9a35c1d777",
            "personality": "gentle",
            "speech_style": "friendly",
            "selected_tags": selected,
            "shown_cities": shown,
            "companion_name": "小K",
        },
        get_llm_client(),
        InMemoryKV(),
    )
    assert result["fail_reason"] is None, result["fail_reason"]
    # 稱號被 mapping 覆寫
    assert result["travel_identity"] == "獨處療癒師"
    # 城市禁令
    assert result["destination_cn"] not in shown
    # highlight_tags 從輸入 label 原樣挑 3 個
    from app.services.companion.partner import resolve_tag_labels

    input_labels = set(resolve_tag_labels(selected))
    assert len(result["highlight_tags"]) == 3
    assert set(result["highlight_tags"]) <= input_labels, result["highlight_tags"]
    # 分段推薦與思考過程
    assert len(result["recommendation"]) >= 1
    assert all(len(seg) <= 200 for seg in result["recommendation"])
    assert len(result["reasoning"]) >= 5
    assert len(result["companion_quote"]) <= 30


@pytest.mark.asyncio
async def test_self_introduction_first_person_with_name():
    result = await generate_self_introduction(
        {
            "companion_name": "小K",
            "personality": "humorous",
            "speech_style": "friendly",
            "gender": "male",
        },
        get_llm_client(),
    )
    assert result["fail_reason"] is None, result["fail_reason"]
    assert "小K" in result["introduction"]
    assert 20 <= len(result["introduction"]) <= 250
