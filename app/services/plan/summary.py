"""travel-summary 聊天室初始化（source-spec 5.1 / 8.6；對照 reference TravelSummaryService.php）。

四入口：
- A quiz_completion：city 權威輸入，LLM 只寫開場白
- A2 from_orders：同上，可帶該筆訂單材料
- B imported_itinerary：LLM 讀行程推斷 city
- C from_zero：不打 LLM，固定文案
"""

import json

from app.core.prompt_loader import render_prompt
from app.services.llm.client import LLMClient, call_and_parse

TASK = "travel_summary"

DEFAULT_COMPANION_NAME = "小旅"

FIXED_WELCOME_TEMPLATE = (
    "嗨，我是{name}！還沒有想法也沒關係，我們可以慢慢聊——想放鬆度假，還是想來點探險？"
    "先告訴我你想去哪，或想要什麼樣的旅行氣氛，我陪你從零開始排。"
)

SOFT_FAIL_SUMMARY = "嗯…我這邊看得不太清楚，能再多說一點你的計畫嗎？"

SUMMARY_MAX_TOKENS = 1000


def _trim(value) -> str:
    return str(value).strip() if isinstance(value, (str, int, float)) else ""


def persona_text(params: dict) -> str:
    """旅伴 persona 段（兩期共用慣例，對照 PHP personaText()）。"""
    name = _trim(params.get("companion_name")) or DEFAULT_COMPANION_NAME
    traits = [t for t in (_trim(params.get("personality")), _trim(params.get("speech_style"))) if t]
    if not traits:
        return f"你的名字是「{name}」。"
    return f"你的名字是「{name}」，人格特質與說話風格關鍵字：{'、'.join(traits)}，請以此語氣說話。"


def authority_city(params: dict) -> str:
    """權威入口（quiz/from_orders）的城市：請求帶什麼回什麼，不經 LLM。"""
    return _trim(params.get("city"))


def _wrap_user_input(payload: dict) -> str:
    return "<<<USER_INPUT\n" + json.dumps(payload, ensure_ascii=False) + "\n>>>END_USER_INPUT"


def build_quiz_user_message(params: dict) -> str:
    return _wrap_user_input(
        {
            "city": authority_city(params),
            "city_image_url": _trim(params.get("city_image_url")),
            "intro_text": _trim(params.get("intro_text")),
            "previous_summary": _trim(params.get("previous_summary")),
            "note": _trim(params.get("note")),
        }
    )


def build_order_pick_user_message(params: dict) -> str:
    order = params.get("order") or {}
    return _wrap_user_input(
        {
            "city": authority_city(params),
            "order": {
                "prod_name": _trim(order.get("prod_name")),
                "package_name": _trim(order.get("package_name")),
                "go_dt": _trim(order.get("go_dt")),
            },
            "previous_summary": _trim(params.get("previous_summary")),
            "note": _trim(params.get("note")),
        }
    )


def build_import_user_text(params: dict) -> str:
    return _wrap_user_input(
        {
            "content": _trim(params.get("content")),
            "has_image": params.get("source_type") == "image",
            "previous_summary": _trim(params.get("previous_summary")),
            "note": _trim(params.get("note")),
        }
    )


async def summarize(params: dict, client: LLMClient) -> dict:
    entry_type = params.get("entry_type")
    if entry_type == "quiz_completion":
        return await _request_summary(
            client,
            system=render_prompt("phase2/summary_quiz", persona=persona_text(params)),
            user=build_quiz_user_message(params),
            authority=authority_city(params),
        )
    if entry_type == "from_orders":
        return await _request_summary(
            client,
            system=render_prompt("phase2/summary_order_pick", persona=persona_text(params)),
            user=build_order_pick_user_message(params),
            authority=authority_city(params),
        )
    if entry_type == "imported_itinerary":
        return await _summarize_from_import(params, client)
    return _fixed_welcome(params)


def _fixed_welcome(params: dict) -> dict:
    """C：從零開始，制式化歡迎語，不打 LLM。"""
    name = _trim(params.get("companion_name")) or DEFAULT_COMPANION_NAME
    return {
        "summary": FIXED_WELCOME_TEMPLATE.format(name=name),
        "city": "",
        "ai_model": None,
        "fail_reason": None,
    }


async def _summarize_from_import(params: dict, client: LLMClient) -> dict:
    system = render_prompt("phase2/summary_import", persona=persona_text(params))
    user_text = build_import_user_text(params)

    content_parts = None
    if params.get("source_type") == "image":
        content_parts = [{"type": "text", "text": user_text}]
        for url in params.get("image_urls") or []:
            content_parts.append({"type": "image_url", "image_url": {"url": str(url)}})

    return await _request_summary(
        client, system=system, user=user_text, authority=None, content_parts=content_parts
    )


async def _request_summary(
    client: LLMClient,
    *,
    system: str,
    user: str,
    authority: str | None,
    content_parts: list[dict] | None = None,
) -> dict:
    """統一送出並解析 {summary, city}。

    authority 非 None（quiz/from_orders 入口）時，回傳 city 一律原樣用它（含軟失敗），
    不採信 LLM 輸出；None（imported_itinerary）才由 LLM 推斷。
    """
    model = client.model_for(TASK)
    result = await call_and_parse(
        client, TASK, system, user, max_tokens=SUMMARY_MAX_TOKENS, content_parts=content_parts
    )

    data = result.data if isinstance(result.data, dict) else None
    summary = _trim((data or {}).get("summary"))

    if result.fail_reason is not None or not summary:
        fail_reason = result.fail_reason or "LLM fail: LLM 回應無法解析為預期 JSON（缺 summary 欄位）"
        return {
            "summary": SOFT_FAIL_SUMMARY,
            "city": authority if authority is not None else "",
            "ai_model": model,
            "fail_reason": fail_reason,
        }

    return {
        "summary": summary,
        "city": authority if authority is not None else _trim(data.get("city")),
        "ai_model": model,
        "fail_reason": None,
    }
