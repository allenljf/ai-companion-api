"""travel-revise：自然語言修改既有行程（source-spec 5.7 / 8.11）。

無狀態設計：App 每輪帶「當前最新完整行程 + 整個聊天室完整對話」，最後一則 user 訊息
= 本次修改需求。回應永遠是完整行程（mode 恆為 "full"），App 整包替換渲染。

與 guide 的關鍵差異（都是後端把關，不信任模型）：
- day 一律依陣列順序重編 1..N（忽略輸入/模型的 day 欄位）——插入/刪除/搬移天全靠陣列順序
- changed_days 後端逐位置 diff，不採信 LLM 自報
- 離題/失敗一律原樣返回「正規化過的輸入行程」（days 不會是空的）；離題不是失敗（照常計費）
- 白名單 = 行程既有 id ∪ 本次帶入 id（少了前半，每次 revise 都會把 id 洗成 null）
"""

import logging

from app.core.normalize import dedupe_tracked_ids, normalize_day_shape, scalar_trim
from app.core.prompt_loader import render_prompt
from app.core.truncate import truncate_at_sentence
from app.core.zh import to_traditional
from app.services.llm.client import LLMClient, call_and_parse
from app.services.plan.guide import convert_day_text
from app.services.plan.persona import persona_text, wrap_user_input

logger = logging.getLogger(__name__)

TASK = "travel_revise"
# 回完整行程（不只是變動天），輸出量跟 travel-guide 相當
REVISE_MAX_TOKENS = 8000
REPLY_MAX_LENGTH = 60

FAILURE_REPLY = "嗯…我這邊卡了一下，再說一次剛剛那句好嗎？"


# ---------------------------------------------------------------------------
# 白名單收集：行程既有 id（itinerary.* 刻意不深驗，必須防非 scalar）
# ---------------------------------------------------------------------------


def allowed_order_oids(params: dict) -> list[str]:
    """本次帶入 orders 的合法 oid（非 scalar 視同沒帶；oid "0" 不被 falsy 吃掉）。"""
    return _collect_ids(params.get("orders"), "oid")


def allowed_product_ids(params: dict) -> list[str]:
    """本次帶入 products 的合法 prod_id（同 oid 的把關方式，走獨立清單）。"""
    return _collect_ids(params.get("products"), "prod_id")


def _collect_ids(entries, key: str) -> list[str]:
    ids = []
    for entry in entries or []:
        value = scalar_trim(entry.get(key) if isinstance(entry, dict) else None)
        if value != "":
            ids.append(value)
    return ids


def booked_orders_payload(params: dict) -> list[dict]:
    """LLM user message 用的 orders payload（材料原樣傳遞，oid 供對映）。"""
    return [
        {
            "oid": str(order.get("oid") or ""),
            "prod_name": str(order.get("prod_name") or ""),
            "package_name": str(order.get("package_name") or ""),
            "destination_name": str(order.get("destination_name") or ""),
            "go_dt": str(order.get("go_dt") or ""),
        }
        for order in params.get("orders") or []
    ]


def selected_products_payload(params: dict) -> list[dict]:
    """LLM user message 用的 products payload（材料原樣傳遞，prod_id 供對映）。"""
    return [
        {
            "prod_id": str(product.get("prod_id") or ""),
            "prod_name": str(product.get("prod_name") or ""),
            "introduction": str(product.get("introduction") or ""),
            "destination_names": [str(n) for n in (product.get("destination_names") or [])],
        }
        for product in params.get("products") or []
    ]


def collect_itinerary_oids(days: list) -> list[str]:
    """從既有行程收集 oid，併入白名單——App 不重帶 orders 也不會弄丟已排入的預訂。"""
    return _collect_item_ids(days, "oid")


def collect_itinerary_product_ids(days: list) -> list[str]:
    """從既有行程收集 prod_id——少了這一步，每次 revise 都會把 prod_id 洗成 null。"""
    return _collect_item_ids(days, "prod_id")


def _collect_item_ids(days: list, key: str) -> list[str]:
    seen: dict[str, None] = {}
    for day in days:
        items = day.get("items") if isinstance(day, dict) else None
        for item in items if isinstance(items, list) else []:
            value = scalar_trim(item.get(key) if isinstance(item, dict) else None)
            if value != "":
                seen.setdefault(value, None)
    return list(seen)


# ---------------------------------------------------------------------------
# sequential 重編（與 guide 的「優先信模型 day」刻意不同）
# ---------------------------------------------------------------------------


def normalize_sequential_days(
    days: list, allowed_oids: list[str], allowed_product_ids_: list[str]
) -> list[dict]:
    """逐日正規化，day 一律依陣列順序重編 1..N（忽略任何既有 day 欄位）——
    插入/刪除/搬移天這類結構性變更交給「陣列順序」處理，不依賴 LLM 或 App 算編號。"""
    normalized = [
        normalize_day_shape(
            convert_day_text(day if isinstance(day, dict) else {}),
            index + 1,
            allowed_oids,
            allowed_product_ids_,
        )
        for index, day in enumerate(days)
    ]
    return dedupe_tracked_ids(normalized)


def diff_changed_days(original_days: list[dict], revised_days: list[dict]) -> list[int]:
    """逐位置比對輸入/輸出，算出真的有變動的天（不採信 LLM 自報的編號）。

    純粹是 UI 高亮提示：回應已是完整行程，App 整包重渲染結果也一樣正確。
    """
    changed = []
    for index, day in enumerate(revised_days):
        original = original_days[index] if index < len(original_days) else None
        same = original is not None and all(
            original[key] == day[key] for key in ("items", "status", "kind", "half_day")
        )
        if not same:
            changed.append(day["day"])
    return changed


# ---------------------------------------------------------------------------
# prompt 組裝
# ---------------------------------------------------------------------------


def build_system_prompt(
    params: dict, allowed_oids: list[str], allowed_product_ids_: list[str]
) -> str:
    # 注入條件是「白名單有值」（行程既有 id 也算），不是 request 有沒有帶 orders/products
    return render_prompt(
        "phase2/revise",
        persona=persona_text(params),
        target_day=params.get("target_day"),
        booked_orders_section=(
            render_prompt("phase2/revise_orders_section") if allowed_oids else ""
        ),
        selected_products_section=(
            render_prompt("phase2/revise_products_section") if allowed_product_ids_ else ""
        ),
    )


def build_user_message(params: dict) -> str:
    payload: dict = {
        "itinerary": list(params.get("itinerary") or []),
        "target_day": params.get("target_day"),
        "city": str(params.get("city") or ""),
        "preferences": dict(params.get("preferences") or {}),
        "conversation": [
            {"role": str(m.get("role", "")), "content": str(m.get("content", ""))}
            for m in params.get("messages") or []
        ],
    }
    if params.get("orders"):
        payload["orders"] = booked_orders_payload(params)
    if params.get("products"):
        payload["products"] = selected_products_payload(params)
    return wrap_user_input(payload)


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


async def revise(params: dict, client: LLMClient) -> dict:
    model = client.model_for(TASK)
    itinerary = list(params.get("itinerary") or [])

    oid_whitelist = _union(collect_itinerary_oids(itinerary), allowed_order_oids(params))
    product_whitelist = _union(
        collect_itinerary_product_ids(itinerary), allowed_product_ids(params)
    )
    # 不管成功/離題/失敗都可能需要「原樣返回」，先正規化一次原始輸入
    original_days = normalize_sequential_days(itinerary, oid_whitelist, product_whitelist)

    result = await call_and_parse(
        client,
        TASK,
        build_system_prompt(params, oid_whitelist, product_whitelist),
        build_user_message(params),
        max_tokens=REVISE_MAX_TOKENS,
    )
    if result.fail_reason is not None:
        return _unchanged_result(model, original_days, FAILURE_REPLY, False, result.fail_reason)

    decoded = result.data if isinstance(result.data, dict) else {}
    if not scalar_trim(decoded.get("reply")):
        return _unchanged_result(
            model, original_days, FAILURE_REPLY, False,
            "LLM fail: LLM 回應無法解析為預期 JSON（缺 reply 欄位）",
        )

    reply = truncate_at_sentence(
        to_traditional(scalar_trim(decoded.get("reply"))), REPLY_MAX_LENGTH
    )

    # 離題不是失敗：不採信 LLM 對 itinerary 的任何輸出，原樣返回輸入行程、照常計費
    if decoded.get("off_topic"):
        return _unchanged_result(model, original_days, reply, True, None)

    revised = decoded.get("itinerary")
    if not revised or not isinstance(revised, list):
        return _unchanged_result(
            model, original_days, reply, False,
            "LLM fail: LLM 回應無法解析為預期 JSON（缺 itinerary 欄位）",
        )

    # 城市一致性保證（技法 5）：先轉繁再比對，防簡體拼寫繞過
    requested_city = str(params.get("city") or "").strip()
    output_city = to_traditional(scalar_trim(decoded.get("city"))) or requested_city
    if requested_city and output_city and output_city != requested_city:
        return _unchanged_result(
            model, original_days, reply, False,
            f"LLM fail: 輸出城市「{output_city}」與請求城市「{requested_city}」不一致",
        )

    revised_days = normalize_sequential_days(revised, oid_whitelist, product_whitelist)
    return _revised_result(model, reply, output_city, revised_days, original_days, decoded)


def _union(first: list[str], second: list[str]) -> list[str]:
    return list(dict.fromkeys([*first, *second]))


def _revised_result(
    model: str, reply: str, output_city: str,
    revised_days: list[dict], original_days: list[dict], decoded: dict,
) -> dict:
    unplanned_days = [int(d) for d in decoded.get("unplanned_days") or [] if _is_intish(d)]
    total_days = len(revised_days)
    phase = "done" if not unplanned_days and revised_days else "plan"
    changed_summary = to_traditional(scalar_trim(decoded.get("changed_summary")))

    return {
        "city": output_city or None,
        "days": total_days or None,
        "date_range": None,
        "days_provisional": True,
        "phase": phase,
        "unplanned_days": unplanned_days,
        "pending_fields": [to_traditional(str(f)) for f in decoded.get("pending_fields") or []],
        "reply": reply,
        "changed_summary": changed_summary or None,
        "off_topic": False,
        "itinerary_patch": {
            "mode": "full",
            "changed_days": diff_changed_days(original_days, revised_days),
            "days": revised_days,
        },
        "progress_label": _progress_label(revised_days, unplanned_days, total_days),
        "chips": {"mode": "hidden", "items": []},
        "main_action": {"type": "view_trip", "label": "看看完整行程"} if phase == "done" else None,
        "ai_model": model,
        "fail_reason": None,
    }


def _unchanged_result(
    model: str, original_days: list[dict], reply: str, off_topic: bool, fail_reason: str | None
) -> dict:
    """離題／失敗共用：原樣返回輸入行程，changed_days 恆為空——「full 但沒有變動」，
    App 一律直接渲染 itinerary_patch.days，不需要為失敗/離題另寫分支保留舊資料。"""
    total_days = len(original_days)
    phase = "done" if original_days else "plan"

    return {
        "city": None,
        "days": total_days or None,
        "date_range": None,
        "days_provisional": True,
        "phase": phase,
        "unplanned_days": [],
        "pending_fields": [],
        "reply": reply,
        "changed_summary": None,
        "off_topic": off_topic,
        "itinerary_patch": {"mode": "full", "changed_days": [], "days": original_days},
        "progress_label": _progress_label(original_days, [], total_days),
        "chips": {"mode": "hidden", "items": []},
        "main_action": {"type": "view_trip", "label": "看看完整行程"} if phase == "done" else None,
        "ai_model": model,
        "fail_reason": fail_reason,
    }


def _progress_label(days: list[dict], unplanned_days: list[int], total_days: int) -> str:
    planned = len(days) - len(unplanned_days)
    total = total_days or len(days)
    return f"已排 {planned}/{total} 天"


def _is_intish(value) -> bool:
    if isinstance(value, bool):
        return False
    try:
        int(value)
        return True
    except (TypeError, ValueError):
        return False
