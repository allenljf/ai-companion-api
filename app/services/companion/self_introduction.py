"""self-introduction：旅伴建立完成頁的自我介紹（source-spec 4.7）。

- system prompt 以 data/ai_partner.json 的 partner_intro_prompt 為主，
  缺值才 fallback 回 app/prompts/phase1/self_introduction.txt（對照 PHP 的 DCS 優先邏輯）
- 純文字輸出（不開 json_mode、不走 call_and_parse）
- 失敗（拋錯或空字串）→ fallback 固定文案 + fail_reason
"""

import json
import logging

from app.core.prompt_loader import render_prompt
from app.core.zh import to_traditional
from app.services.companion.partner import (
    load_ai_partner,
    resolve_gender_text,
    resolve_partner_text,
)
from app.services.llm.client import LLMClient

logger = logging.getLogger(__name__)

TASK = "self_introduction"
INTRO_MAX_TOKENS = 800


def _system_prompt() -> str:
    configured = str(load_ai_partner().get("partner_intro_prompt") or "").strip()
    return configured or render_prompt("phase1/self_introduction")


def build_intro_user_message(params: dict) -> str:
    partner = resolve_partner_text(params.get("personality", ""), params.get("speech_style", ""))
    payload = {
        "companion_name": str(params.get("companion_name") or ""),
        "gender": resolve_gender_text(str(params.get("gender") or "")),
        "speech_style": partner["speech_style_text"],
        "personality": partner["personality_text"],
    }
    # 對照 PHP：此支獨用 JSON_PRETTY_PRINT
    return "<<<USER_INPUT\n" + json.dumps(payload, ensure_ascii=False, indent=4) + "\n>>>END_USER_INPUT"


async def generate_self_introduction(params: dict, client: LLMClient) -> dict:
    companion_name = str(params.get("companion_name") or "")
    introduction = ""
    fail_reason = None

    try:
        raw = await client.chat(
            TASK, _system_prompt(), build_intro_user_message(params),
            max_tokens=INTRO_MAX_TOKENS, json_mode=False,
        )
        introduction = to_traditional(raw.strip())
    except Exception as exc:
        logger.error("self-introduction LLM failed", extra={"error": str(exc)})
        fail_reason = f"LLM fail: {type(exc).__name__}: {exc}"

    if not introduction:
        if fail_reason is None:
            fail_reason = "LLM fail: LLM 回應為空字串"
        introduction = (
            f"嗨！我是{companion_name}，一個喜歡隨性探索、享受獨處時光的旅伴。"
            "我們會一起發現那些地圖上找不到的角落，用你自己的步調感受每一個城市的溫度。準備好了嗎？"
        )

    return {
        "introduction": introduction,
        "ai_model": client.model_for(TASK),
        "fail_reason": fail_reason,
    }
