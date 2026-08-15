"""quiz：加權隨機選題 + LLM 只改寫語氣（source-spec 4.2，對照 CompanionService::fetchQuiz）。

技法 1 最純粹的例子：選題（權重計算）與結構維護歸程式，LLM 只換詞。
LLM 完全失效時測驗仍 100% 可用（fallback 原始文案），AI 是加值而非依賴。
"""

import json
import logging
import random

from app.core.prompt_loader import render_prompt
from app.core.zh import to_traditional
from app.services.companion.partner import load_quiz_dimensions, resolve_partner_text
from app.services.llm.client import LLMClient, call_and_parse
from app.services.plan.persona import wrap_user_input

logger = logging.getLogger(__name__)

TASK = "quiz"
REWRITE_MAX_TOKENS = 1500


def select_one_per_dimension(
    shown_question_counts: dict, dimensions: list[dict], rng: random.Random
) -> list[dict]:
    """每維度依權重 1/2^出現次數 抽 1 題；選項附掛 tag 物件。不修改來源資料。"""
    if not dimensions:
        raise ValueError("quiz dimensions empty (C005 QUIZ_THEME_NOT_FOUND)")

    selected = []
    for dimension in dimensions:
        quizzes = dimension.get("quizzes") or []
        if not quizzes:
            continue

        tag_map = {tag["id"]: tag for tag in dimension.get("tags") or []}
        weights = [
            1.0 / (2 ** int(shown_question_counts.get(quiz["id"], 0) or 0)) for quiz in quizzes
        ]

        threshold = rng.random() * sum(weights)
        cumulative = 0.0
        for quiz, weight in zip(quizzes, weights):
            cumulative += weight
            if threshold <= cumulative:
                picked = dict(quiz)
                picked["options"] = [
                    option | {"tag": tag_map[option["tag_id"]]}
                    if option.get("tag_id") in tag_map
                    else dict(option)
                    for option in quiz.get("options") or []
                ]
                selected.append(picked)
                break
    return selected


def merge_rewritten_text(originals: list[dict], rewritten: list) -> list[dict]:
    """只取 LLM 回的 text 欄位合併回原題目，其他欄位一律用原始值；文字轉繁（風險 #3）。"""
    rewritten_map = {}
    for quiz in rewritten:
        if not isinstance(quiz, dict):
            continue
        options = {
            option.get("index"): str(option.get("text", ""))
            for option in quiz.get("options") or []
            if isinstance(option, dict)
        }
        rewritten_map[quiz.get("id")] = {"text": str(quiz.get("text", "")), "options": options}

    merged = []
    for original in originals:
        entry = rewritten_map.get(original["id"])
        quiz = dict(original)
        if entry:
            quiz["text"] = to_traditional(entry["text"]) or original["text"]
            quiz["options"] = [
                dict(option) | {"text": to_traditional(entry["options"][option["index"]])}
                if option.get("index") in entry["options"]
                else dict(option)
                for option in original["options"]
            ]
        merged.append(quiz)
    return merged


def build_rewrite_system_prompt(params: dict) -> str:
    partner = resolve_partner_text(params.get("personality", ""), params.get("speech_style", ""))
    return render_prompt(
        "phase1/quiz_rewrite",
        personality_text=partner["personality_text"],
        speech_style_text=partner["speech_style_text"],
    )


def build_rewrite_user_message(questions: list[dict]) -> str:
    data = [
        {
            "id": quiz["id"],
            "text": quiz["text"],
            "options": [
                {"index": option["index"], "text": option["text"]} for option in quiz["options"]
            ],
        }
        for quiz in questions
    ]
    return wrap_user_input({"questions": data})


async def fetch_quiz(params: dict, client: LLMClient, rng: random.Random | None = None) -> dict:
    questions = select_one_per_dimension(
        dict(params.get("shown_question_counts") or {}), load_quiz_dimensions(), rng or random.Random()
    )

    result = await call_and_parse(
        client, TASK, build_rewrite_system_prompt(params), build_rewrite_user_message(questions),
        max_tokens=REWRITE_MAX_TOKENS,
    )

    decoded = result.data if isinstance(result.data, dict) else {}
    if result.fail_reason is not None:
        fail_reason = result.fail_reason
    elif "questions" not in decoded:
        fail_reason = "LLM 回應無法解析為預期 JSON（缺 questions 欄位）"
    else:
        fail_reason = None
        questions = merge_rewritten_text(questions, decoded["questions"] or [])

    stripped = [
        quiz | {"options": [{k: v for k, v in option.items() if k != "tag_id"}
                            for option in quiz["options"]]}
        for quiz in questions
    ]

    return {
        "count": len(stripped),
        "questions": stripped,
        "ai_model": client.model_for(TASK),
        "fail_reason": fail_reason,
    }
