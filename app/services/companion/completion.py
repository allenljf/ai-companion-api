"""quiz-completions：提交答案 → 規則式八人格 + LLM 文案生成（source-spec 4.3）。

後端把關（對照 CompanionService::completeQuiz）：
- travel_identity 一律以 TravelIdentityMapper 結果**覆寫** LLM 輸出（權威輸入不經 LLM）
- companion_quote ≤30 字、recommendation 每段 ≤200 字（句尾截斷保險）
- 成功才以 completion_uuid 快取分析 24h，供 share-image（階段 8）按需產圖讀回；
  快取寫入失敗不讓整支失敗（文字分析已可回傳）
"""

import logging
import random

from app.core.prompt_loader import render_prompt
from app.core.truncate import truncate_at_sentence
from app.core.zh import to_traditional
from app.services.companion.identity_mapper import map_travel_identity
from app.services.companion.partner import (
    load_quiz_dimensions,
    load_quiz_image_config,
    resolve_partner_text,
    resolve_tag_labels,
)
from app.services.llm.client import LLMClient, call_and_parse
from app.services.plan.persona import wrap_user_input
from app.storage.kv import InMemoryKV

logger = logging.getLogger(__name__)

TASK = "quiz_completions"
# 新版 response_prompt 多了 reasoning（10+ 句）與分段 recommendation，2000 有截斷風險
COMPLETION_MAX_TOKENS = 3000
COMPANION_QUOTE_MAX_LENGTH = 30
RECOMMENDATION_MAX_LENGTH = 200
# 分析快取 TTL：24h，供 POST share-image 按需產圖取用
COMPLETION_CACHE_TTL = 86400
CACHE_KEY_PREFIX = "companion:completion:"


def build_completion_user_message(params: dict, identity: dict) -> str:
    partner = resolve_partner_text(params.get("personality", ""), params.get("speech_style", ""))
    return wrap_user_input(
        {
            "personality": partner["personality_text"],
            "speech_style": partner["speech_style_text"],
            # tag id 解析為繁中 label 才進 prompt（LLM 讀人類可讀文字）
            "selected_tags": resolve_tag_labels(list(params.get("selected_tags") or [])),
            "shown_cities": [str(c) for c in params.get("shown_cities") or []],
            "companion_name": str(params.get("companion_name") or ""),
            # 系統依 tag mapping 決定的旅行人格稱號，LLM 只能沿用、不可自創
            "travel_identity": identity["travel_identity"],
            "travel_identity_en": identity["travel_identity_en"],
        }
    )


async def complete_quiz(params: dict, client: LLMClient, kv: InMemoryKV) -> dict:
    completion_uuid = str(params.get("completion_uuid") or "")
    companion_name = str(params.get("companion_name") or "")

    # 旅行人格稱號不採 LLM 生成，一律依答題 tag mapping 為八人格之一；
    # 一併餵給 LLM，讓 tagline / recommendation 等文案調性呼應此人格
    identity = map_travel_identity(
        list(params.get("selected_tags") or []), load_quiz_dimensions()
    )

    result = await call_and_parse(
        client,
        TASK,
        render_prompt("phase1/quiz_completion"),
        build_completion_user_message(params, identity),
        max_tokens=COMPLETION_MAX_TOKENS,
    )
    fail_reason = result.fail_reason
    analysis = result.data if isinstance(result.data, dict) else {}
    if fail_reason is None and not analysis:
        fail_reason = "LLM fail: LLM 回應無法解析為預期 JSON 或為空"

    # 覆寫 LLM 回傳的人格稱號：只允許 mapping 出的八人格之一（快取後供分享海報取用）
    analysis["travel_identity"] = identity["travel_identity"]
    analysis["travel_identity_en"] = identity["travel_identity_en"]

    companion_quote = truncate_at_sentence(
        to_traditional(_text(analysis.get("companion_quote"))), COMPANION_QUOTE_MAX_LENGTH
    )
    highlight_tags = [to_traditional(_text(t)) for t in list(analysis.get("highlight_tags") or [])[:3]]

    # 新版 prompt 回分段陣列；相容舊版回單一字串（包成單元素陣列）
    recommendation_raw = analysis.get("recommendation") or []
    if not isinstance(recommendation_raw, list):
        recommendation_raw = [recommendation_raw]
    recommendation = [
        truncate_at_sentence(to_traditional(_text(segment)), RECOMMENDATION_MAX_LENGTH)
        for segment in recommendation_raw
        if _text(segment)
    ]

    # 產圖等待畫面的 AI 思考過程逐句陣列；未回傳或格式不符 → 空陣列
    reasoning = [to_traditional(_text(line)) for line in (analysis.get("reasoning") or []) if _text(line)]

    # 分析成功才快取（供 share-image 按需產圖）；寫入失敗只記錄、不讓整支失敗
    if fail_reason is None:
        try:
            await kv.set(
                CACHE_KEY_PREFIX + completion_uuid,
                {
                    "analysis": analysis,
                    "companion_name": companion_name,
                    "partner_avatar_url": str(params.get("partner_avatar_url") or "") or None,
                },
                COMPLETION_CACHE_TTL,
            )
        except Exception:
            logger.warning("cache completion analysis failed", extra={"uuid": completion_uuid})

    image_config = load_quiz_image_config()

    return {
        "quiz_completion_id": random.randint(1, 0x7FFFFFFF),
        "travel_identity": analysis["travel_identity"],
        "travel_identity_en": analysis["travel_identity_en"],
        "destination_cn": to_traditional(_text(analysis.get("destination_cn"))),
        "destination_en": _text(analysis.get("destination_en")),
        "destination_country": to_traditional(_text(analysis.get("destination_country"))),
        "destination_country_en": _text(analysis.get("destination_country_en")),
        "tagline": to_traditional(_text(analysis.get("tagline"))),
        "tagline_en": _text(analysis.get("tagline_en")),
        "highlight_tags": highlight_tags,
        "highlight_tags_en": [_text(t) for t in analysis.get("highlight_tags_en") or []],
        "companion_quote": companion_quote,
        "companion_quote_en": _text(analysis.get("companion_quote_en")),
        "recommendation": recommendation,
        "reasoning": reasoning,
        "social_post": to_traditional(_text(analysis.get("social_post"))),
        # 產圖按需：pending=可呼叫 share-image 產圖；skipped=分析失敗、無從產圖
        "share_image_status": "pending" if fail_reason is None else "skipped",
        "ai_model": client.model_for(TASK),
        "share_image_ai_model": _hero_model(),
        "share_image_size": str(image_config.get("image_size") or ""),
        "share_image_quality": str(image_config.get("image_quality") or ""),
        "fail_reason": fail_reason,
        "art_style_version": image_config.get("art_style_version"),
        "poster_prompt_version": image_config.get("poster_prompt_version"),
        "response_prompt_version": image_config.get("response_prompt_version"),
    }


def _hero_model() -> str:
    from app.services.companion.share_image import HERO_MODEL

    return HERO_MODEL


def _text(value) -> str:
    return str(value).strip() if isinstance(value, (str, int, float)) and not isinstance(value, bool) else ""
