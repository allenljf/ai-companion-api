"""recommend-city：多輪對話收斂城市（source-spec 5.5 / 8.9）。

無狀態設計：App 每次全量帶入對話歷史，輪次 = messages 中 role=user 的訊息數
（伺服器自己算，不信任 App 自報）。

換城上限：shown_cities 長度天然就是換城次數（App 每換一次累加一個城市），
>5 時不打 LLM 直接回固定文案。

shown_cities 違規：字面比對（trim + 不分大小寫）命中時帶違規回饋自動重試一次
（回饋放 prompt 最結尾——LLM 對結尾指令最敏感）；重試仍違規才回 fail_reason。
"""

import logging

from app.core.prompt_loader import render_prompt
from app.services.llm.client import LLMClient, call_and_parse
from app.services.plan.persona import _trim, persona_text, wrap_user_input

logger = logging.getLogger(__name__)

TASK = "recommend_city"
MAX_ROUNDS = 5
MAX_CITY_SWAPS = 5
SWAP_CHIP_TEXT = "換一個城市"
RESTART_CHIP_TEXT = "重新聊聊"
REMINDER_ROUND = 4
REPLY_MAX_TOKENS = 1000

FAILURE_REPLY = "嗯…我這邊卡了一下，再說一次剛剛那句好嗎？"


def count_rounds(messages: list[dict]) -> int:
    """輪次 = 對話歷史中使用者訊息數（含本次輸入）。"""
    return sum(1 for m in messages if isinstance(m, dict) and m.get("role") == "user")


def is_shown_city(city: str, shown_cities: list[str]) -> bool:
    """字面比對（trim + 不分大小寫）；跨語系同城（京都 vs Kyoto）靠 prompt 禁令把關。"""
    normalized = city.strip().lower()
    return any(str(shown).strip().lower() == normalized for shown in shown_cities)


def build_user_message(messages: list[dict], shown_cities: list[str]) -> str:
    history = [
        {"role": str(m.get("role", "")), "content": str(m.get("content", ""))} for m in messages
    ]
    payload: dict = {"conversation": history}
    if shown_cities:
        payload["shown_cities"] = shown_cities
    return wrap_user_input(payload)


def build_system_prompt(
    params: dict, *, round_: int, shown_cities: list[str], violated_city: str | None
) -> str:
    return render_prompt(
        "phase2/recommend_city",
        persona=persona_text(params),
        round=round_,
        max_rounds=MAX_ROUNDS,
        reminder_round=REMINDER_ROUND,
        remaining=max(0, MAX_ROUNDS - round_),
        city_list="、".join(shown_cities),
        violated_city=violated_city,
    )


async def recommend(params: dict, client: LLMClient) -> dict:
    messages = list(params.get("messages") or [])
    shown_cities = [c for c in (str(s).strip() for s in (params.get("shown_cities") or [])) if c]
    round_ = count_rounds(messages)

    # 換城超過上限：不打 LLM，直接回固定文案（App 端引導重新聊聊）
    if len(shown_cities) > MAX_CITY_SWAPS:
        return _base_result(round_, None) | {
            "reply": f"已經幫你換過 {MAX_CITY_SWAPS} 個城市啦！不如我們重新開始再聊一輪吧，說不定會聊出新靈感。",
            "quick_replies": [RESTART_CHIP_TEXT],
            "swap_limit_reached": True,
        }

    model = client.model_for(TASK)
    resolution = await _resolve_recommendation(params, messages, shown_cities, round_, client)
    if "error" in resolution:
        return _base_result(round_, model) | {
            "reply": FAILURE_REPLY,
            "fail_reason": resolution["error"],
        }

    decoded, city = resolution["decoded"], resolution["city"]
    return _base_result(round_, model) | {
        "reply": _trim(decoded.get("reply")),
        "quick_replies": _build_quick_replies(decoded, city, len(shown_cities)),
        "recommended_city": city,
        "city_reason": _trim(decoded.get("city_reason")),
        "is_final": city != "",
        "off_topic": bool(decoded.get("off_topic", False)),
    }


async def _resolve_recommendation(
    params: dict, messages: list[dict], shown_cities: list[str], round_: int, client: LLMClient
) -> dict:
    """呼叫 LLM 並驗證（含 shown_cities 違規自動重試一次）。

    回傳 {"decoded": dict, "city": str} 或 {"error": 失敗原因}。
    """
    result = await _request_reply(params, messages, shown_cities, round_, client, None)
    if result.fail_reason is not None:
        return {"error": result.fail_reason}

    decoded = result.data if isinstance(result.data, dict) else {}
    if not _trim(decoded.get("reply")):
        return {"error": "LLM fail: LLM 回應無法解析為預期 JSON（缺 reply 欄位）"}

    city = _trim(decoded.get("recommended_city"))

    # 強制收斂輪 LLM 仍沒給城市 → 視為失敗（App 原樣重打即可，無副作用）
    if round_ >= MAX_ROUNDS and city == "":
        return {"error": "LLM fail: 已達強制收斂輪但 LLM 未輸出 recommended_city"}

    # shown_cities 硬檢查：命中帶違規回饋重試一次
    if city and is_shown_city(city, shown_cities):
        logger.warning(
            "shown_cities violated, auto retry", extra={"round": round_, "city": city}
        )
        retry = await _request_reply(params, messages, shown_cities, round_, client, city)
        if retry.fail_reason is not None:
            return {"error": f"LLM fail: 重試呼叫失敗（{retry.fail_reason}）"}

        decoded = retry.data if isinstance(retry.data, dict) else {}
        city = _trim(decoded.get("recommended_city"))
        if not _trim(decoded.get("reply")) or city == "" or is_shown_city(city, shown_cities):
            return {"error": "LLM fail: 重試後推薦城市仍與 shown_cities 重複"}

    return {"decoded": decoded, "city": city}


async def _request_reply(params, messages, shown_cities, round_, client, violated_city):
    system = build_system_prompt(
        params, round_=round_, shown_cities=shown_cities, violated_city=violated_city
    )
    return await call_and_parse(
        client, TASK, system, build_user_message(messages, shown_cities),
        max_tokens=REPLY_MAX_TOKENS,
    )


def _build_quick_replies(decoded: dict, city: str, shown_city_count: int) -> list[str]:
    """收斂後 chips 後端強制固定（App 用文字比對綁行為）；未收斂用 LLM 的、上限 4。

    本次已帶滿上限數量的 shown_cities（= 這是最後一次換城）時不再遞「換一個城市」。
    """
    if city == "":
        raw = decoded.get("quick_replies")
        return [str(q) for q in raw][:4] if isinstance(raw, list) else []

    if shown_city_count >= MAX_CITY_SWAPS:
        return [f"就去{city}！", RESTART_CHIP_TEXT]
    return [f"就去{city}！", SWAP_CHIP_TEXT, RESTART_CHIP_TEXT]


def _base_result(round_: int, model: str | None) -> dict:
    return {
        "reply": "",
        "quick_replies": [],
        "recommended_city": "",
        "city_reason": "",
        "is_final": False,
        "off_topic": False,
        "round": round_,
        "max_rounds": MAX_ROUNDS,
        "remaining_rounds": max(0, MAX_ROUNDS - round_),
        "swap_limit_reached": False,
        "ai_model": model,
        "fail_reason": None,
    }
