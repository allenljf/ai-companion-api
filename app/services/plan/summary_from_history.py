"""travel-summary-from-history：從瀏覽/購買紀錄判斷城市選項（source-spec 5.4 / 8.8）。

刻意不與 summary_from_wish 共用實作（即使目前邏輯幾乎一樣）：
瀏覽紀錄含隨手看過、已購買的商品，訊號雜訊比與願望清單（主動收藏）不同，
未來判讀邏輯可能分別演進（如對已購買加權重）。兩邊只有 prompt 措辭與兜底文案不同。

資料限制：目前假資料的 purchase_type/purchase_date 全是 null，無法區分「瀏覽過」vs「已購買」。
"""

from app.core.prompt_loader import render_prompt
from app.services.llm.client import LLMClient, call_and_parse
from app.services.plan.persona import _trim, companion_name, persona_text, wrap_user_input

TASK = "travel_summary_from_history"
MAX_TOKENS = 800
MAX_CITIES = 3
MAX_CITY_LEN = 20


def fallback_greeting(params: dict) -> str:
    name = companion_name(params)
    return f"不好意思，我沒辦法從你的瀏覽紀錄判斷出目的地，要不要直接開始規劃？（我是{name}，隨時可以陪你聊）"


def build_user_message(products: list[dict]) -> str:
    # 明示 index；prod_id 刻意不送進 prompt（技法 2）
    payload = [
        {
            "index": i,
            "prod_name": _trim(product.get("prod_name")),
            "introduction": _trim(product.get("introduction")),
            "destination_names": [str(d) for d in (product.get("destination_names") or [])],
        }
        for i, product in enumerate(products)
    ]
    return wrap_user_input({"products": payload})


def normalize_cities(decoded, products: list[dict]) -> list[dict] | None:
    """None = 格式錯誤（llm_error）；空陣列 = low_confidence（由呼叫端判斷）。"""
    if not isinstance(decoded, dict) or not isinstance(decoded.get("cities"), list):
        return None

    seen_cities: set[str] = set()
    used_indexes: set[int] = set()
    normalized: list[dict] = []

    for entry in decoded["cities"]:
        if not isinstance(entry, dict):
            continue
        city = _trim(entry.get("city")) if isinstance(entry.get("city"), (str, int, float)) else ""
        if not city:
            continue

        key = city.lower()
        if key in seen_cities:
            continue
        seen_cities.add(key)

        normalized.append(
            {
                "city": city[:MAX_CITY_LEN],
                "products": _resolve_products(entry.get("product_indexes"), products, used_indexes),
            }
        )
        if len(normalized) >= MAX_CITIES:
            break

    return normalized


def _resolve_products(indexes, products: list[dict], used_indexes: set[int]) -> list[dict]:
    """位置索引 → 權威商品資料。超範圍/非數字忽略；同索引全回應只認第一次。"""
    resolved = []
    if not isinstance(indexes, list):
        return resolved

    for index in indexes:
        if isinstance(index, bool) or not isinstance(index, (int, float, str)):
            continue
        try:
            index = int(index)
        except (TypeError, ValueError):
            continue
        if index < 0 or index >= len(products) or index in used_indexes:
            continue
        used_indexes.add(index)

        product = products[index] or {}
        resolved.append(
            {
                "prod_id": _trim(product.get("prod_id")),
                "prod_name": _trim(product.get("prod_name")),
            }
        )
    return resolved


async def summarize_from_history(params: dict, client: LLMClient) -> dict:
    products = list(params.get("products") or [])
    model = client.model_for(TASK)

    system = render_prompt(
        "phase2/from_history", persona=persona_text(params), name=companion_name(params)
    )
    result = await call_and_parse(
        client, TASK, system, build_user_message(products), max_tokens=MAX_TOKENS
    )

    if result.fail_reason is not None:
        return _fail_result(params, model, "llm_error")

    cities = normalize_cities(result.data, products)
    if cities is None:
        return _fail_result(params, model, "llm_error")
    if not cities:
        return _fail_result(params, model, "low_confidence")

    greeting = _trim(result.data.get("greeting")) or fallback_greeting(params)
    return {"greeting": greeting, "cities": cities, "ai_model": model, "fail_reason": None}


def _fail_result(params: dict, model: str, fail_reason: str) -> dict:
    return {
        "greeting": fallback_greeting(params),
        "cities": [],
        "ai_model": model,
        "fail_reason": fail_reason,
    }
