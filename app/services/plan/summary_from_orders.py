"""travel-summary-from-orders：帶訂單開場（source-spec 5.2 / 8.7）。

逐筆對應：LLM 只輸出 cities 純字串陣列（與輸入訂單一一對應），
order_index 由後端依位置指定（不採信 LLM 自報索引）。
筆數不符 = 位置對應的前提破了，整包視為 llm_error。
"""

from app.core.prompt_loader import render_prompt
from app.services.llm.client import LLMClient, call_and_parse
from app.services.plan.persona import _trim, companion_name, persona_text, wrap_user_input

TASK = "travel_summary_from_orders"
MAX_TOKENS = 400
MAX_CITY_LEN = 20


def fallback_greeting(params: dict) -> str:
    name = companion_name(params)
    return f"不好意思，我沒辦法從你的訂單判斷出目的地，要不要直接開始規劃？（我是{name}，隨時可以陪你聊）"


def build_user_message(orders: list[dict]) -> str:
    # 只送三項判讀材料；oid 等識別碼絕不進 prompt（source-spec 技法 2）
    payload = [
        {
            "prod_name": _trim(order.get("prod_name")),
            "package_name": _trim(order.get("package_name")),
            "destination_name": _trim(order.get("destination_name")),
        }
        for order in orders
    ]
    return wrap_user_input({"orders": payload})


def normalize_cities(decoded, expected_count: int) -> list[str] | None:
    """回傳 None 代表格式錯誤（llm_error）；空字串元素代表該筆判斷不出城市。"""
    if not isinstance(decoded, dict) or not isinstance(decoded.get("cities"), list):
        return None

    cities = decoded["cities"]
    if len(cities) != expected_count:
        return None

    return [(_trim(c) if isinstance(c, str) else "")[:MAX_CITY_LEN] for c in cities]


async def summarize_from_orders(params: dict, client: LLMClient) -> dict:
    orders = list(params.get("orders") or [])
    model = client.model_for(TASK)

    system = render_prompt(
        "phase2/from_orders", persona=persona_text(params), name=companion_name(params)
    )
    result = await call_and_parse(
        client, TASK, system, build_user_message(orders), max_tokens=MAX_TOKENS
    )

    if result.fail_reason is not None:
        return _fail_result(params, model, "llm_error")

    cities = normalize_cities(result.data, len(orders))
    if cities is None:
        return _fail_result(params, model, "llm_error")

    if all(city == "" for city in cities):
        return _fail_result(params, model, "low_confidence")

    greeting = _trim(result.data.get("greeting")) or fallback_greeting(params)
    return {
        "greeting": greeting,
        "options": [{"order_index": i, "city": city} for i, city in enumerate(cities)],
        "ai_model": model,
        "fail_reason": None,
    }


def _fail_result(params: dict, model: str, fail_reason: str) -> dict:
    return {
        "greeting": fallback_greeting(params),
        "options": [],
        "ai_model": model,
        "fail_reason": fail_reason,
    }
