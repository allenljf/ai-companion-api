"""travel-guide：問卷完成後一次性產出完整逐日行程（source-spec 5.6 / 8.10）。

無狀態設計：summary/preferences 由 App 帶入，單次完整生成、不多輪追問。
2026-08-16 起不再接受 orders/products（獨立 App 不帶訂單/商品資訊），
item 的 oid/prod_id 欄位保留在契約中但恆為 null。

後端把關（不信任模型自律）：
- 城市一致性：請求帶 city 時輸出必須一致，不一致視為失敗（不回半新半舊的結果）
- oid/prod_id 白名單 + 全行程去重、booked_anchor 推導 → app.core.normalize
- messages 最多 2 則、單則 ≤60 字（句尾截斷保險）
- 簡轉繁後處理（風險 #3）：城市名先轉繁再比對，防簡體拼寫繞過一致性檢查
"""

import logging

from app.core.normalize import dedupe_tracked_ids, normalize_day_shape, scalar_trim
from app.core.prompt_loader import render_prompt
from app.core.truncate import truncate_at_sentence
from app.core.zh import to_traditional
from app.services.llm.client import LLMClient, call_and_parse
from app.services.plan.persona import persona_text, wrap_user_input

logger = logging.getLogger(__name__)

TASK = "travel_guide"
# 完整行程（逐日 items 含經緯度簡介）輸出 JSON 較大，給足 token 避免截斷
GUIDE_MAX_TOKENS = 8000
# 訊息字數保險（契約「單則 ≤60 字」，LLM 沒守住時後端截斷）
MESSAGE_MAX_LENGTH = 60

FAILURE_MESSAGE = "嗯…行程排到一半卡住了，要不要再試一次？"
DEFAULT_COMPLETION_MESSAGE = "行程排好了！"


# ---------------------------------------------------------------------------
# prompt 組裝
# ---------------------------------------------------------------------------


def build_system_prompt(params: dict) -> str:
    return render_prompt("phase2/guide", persona=persona_text(params))


def build_user_message(params: dict) -> str:
    return wrap_user_input(
        {
            "summary": str(params.get("summary") or ""),
            "city": str(params.get("city") or ""),
            "preferences": dict(params.get("preferences") or {}),
        }
    )


# ---------------------------------------------------------------------------
# LLM 輸出 → 回應包裝
# ---------------------------------------------------------------------------


async def generate(
    params: dict, client: LLMClient, *, image_provider=None, image_store=None
) -> dict:
    model = client.model_for(TASK)

    result = await call_and_parse(
        client, TASK, build_system_prompt(params), build_user_message(params),
        max_tokens=GUIDE_MAX_TOKENS,
    )
    if result.fail_reason is not None:
        return _failure_result(model, result.fail_reason)

    decoded = result.data if isinstance(result.data, dict) else {}
    itinerary = decoded.get("itinerary")
    if not itinerary or not isinstance(itinerary, list):
        return _failure_result(model, "LLM fail: LLM 回應無法解析為預期 JSON（缺 itinerary 欄位）")

    # 城市一致性保證（技法 5）：請求已指定城市時輸出必須一致，不一致視為失敗。
    # 先轉繁再比對——簡體「东京」不能繞過「東京」的一致性檢查
    requested_city = str(params.get("city") or "").strip()
    output_city = to_traditional(scalar_trim(decoded.get("city"))) or requested_city
    if requested_city and output_city and output_city != requested_city:
        return _failure_result(
            model, f"LLM fail: 輸出城市「{output_city}」與請求城市「{requested_city}」不一致"
        )

    success = _success_result(decoded, output_city, model)
    # 行程 hero 圖（行程本體成功才產）：產圖失敗只降級 null，不影響行程回應
    success["hero_image_url"] = await _generate_hero_image(
        output_city, image_provider, image_store
    )
    return success


def _success_result(decoded: dict, output_city: str, model: str) -> dict:
    # 白名單一律為空：本 API 不再接受 orders/products（獨立 App 不帶這些資訊），
    # 因此 LLM 若幻覺出 oid/prod_id 會被 normalize 全數濾成 null（契約欄位形狀不變）
    days = normalize_days(decoded.get("itinerary") or [], [], [])
    unplanned_days = [int(d) for d in decoded.get("unplanned_days") or [] if _is_intish(d)]
    total_days = _coerce_int(decoded.get("days")) or len(days)
    phase = "done" if not unplanned_days and days else "plan"

    return {
        "city": output_city or None,
        "days": total_days or None,
        "date_range": _normalize_date_range(decoded.get("date_range")),
        "days_provisional": True,
        "phase": phase,
        "unplanned_days": unplanned_days,
        "pending_fields": [to_traditional(str(f)) for f in decoded.get("pending_fields") or []],
        "messages": normalize_messages(decoded.get("messages") or []),
        "itinerary_patch": {
            "mode": "full",
            "changed_days": [day["day"] for day in days],
            "days": days,
        },
        "progress_label": _progress_label(days, unplanned_days, total_days),
        "chips": {"mode": "hidden", "items": []},
        "main_action": {"type": "view_trip", "label": "看看完整行程"} if phase == "done" else None,
        "ai_model": model,
        "fail_reason": None,
    }


def build_guide_hero_prompt(city: str) -> str:
    """行程 hero 圖三層 prompt：與 Phase 1 share-image 同風格——
    第 1 層同一份 art_style（anime）、第 3 層同一組 HERO 硬約束（無文字），
    第 2 層主體換成「行程主目的地」場景（phase2/guide_hero_image.txt）。"""
    from app.services.companion.share_prompts import HERO_HARD_CONSTRAINTS, _compose

    body = render_prompt("phase2/guide_hero_image").strip().replace("{city}", city)
    return _compose(body, HERO_HARD_CONSTRAINTS)


# 橫幅 16:9（Gemini → 2K）：行程頁是橫幅版位，與 Phase 1 海報 hero 的直式 9:16 不同
GUIDE_HERO_WIDTH, GUIDE_HERO_HEIGHT = 2048, 1152


def guide_hero_key(city: str) -> str:
    """以城市為 key 跨用戶共用快取（同城市只產一次）；prompt/模型變動自動換 key。"""
    import hashlib

    from app.config import settings

    version = hashlib.md5(
        "|".join([
            settings.image_hero_model,
            f"{GUIDE_HERO_WIDTH}x{GUIDE_HERO_HEIGHT}",
            hashlib.sha256(build_guide_hero_prompt(city).encode()).hexdigest()[:12],
        ]).encode()
    ).hexdigest()[:8]
    city_slug = hashlib.md5(city.encode()).hexdigest()[:12]
    return f"guide-hero/{city_slug}-{version}.png"


async def _generate_hero_image(city: str, image_provider, image_store) -> str | None:
    """有接產圖 provider 且行程有城市才產；失敗（含 429）一律降級 null、不重試
    ——guide 是同步等待的請求，不值得為配圖拉長延遲，App 拿 null 就不顯圖。"""
    if image_provider is None or image_store is None or not city:
        return None
    key = guide_hero_key(city)
    try:
        if await image_store.exists(key):
            return image_store.url(key)
        from app.config import settings
        from app.services.image.client import image_content_type

        data = await image_provider.generate(
            build_guide_hero_prompt(city),
            model=settings.image_hero_model,
            width=GUIDE_HERO_WIDTH,
            height=GUIDE_HERO_HEIGHT,
        )
        return await image_store.upload(key, data, content_type=image_content_type(data))
    except Exception as exc:
        logger.warning("guide hero image failed: %s: %s", city, exc)
        return None


def _failure_result(model: str, fail_reason: str) -> dict:
    return {
        "city": None,
        "hero_image_url": None,
        "days": None,
        "date_range": None,
        "days_provisional": True,
        "phase": "plan",
        "unplanned_days": [],
        "pending_fields": [],
        "messages": [{"type": "statement", "text": FAILURE_MESSAGE}],
        "itinerary_patch": {"mode": "full", "changed_days": [], "days": []},
        "progress_label": "",
        "chips": {"mode": "hidden", "items": []},
        "main_action": None,
        "ai_model": model,
        "fail_reason": fail_reason,
    }


def normalize_days(
    days: list, oid_whitelist: list[str], product_id_whitelist: list[str]
) -> list[dict]:
    """逐日正規化：day 編號優先採用模型輸出、缺則用陣列索引遞增（對照 PHP normalizeDays），
    再做全行程 oid/prod_id 去重（各自獨立、只認第一次）。

    防呆（與 PHP 刻意不同）：模型給的編號不唯一時（實測 llama-3.3-70b 會把最後一天
    拆成兩個同號區塊），一律改用陣列索引重編，避免 App 出現兩個相同的 Day。
    """
    days = [day if isinstance(day, dict) else {} for day in days]
    numbers = [_coerce_int(day.get("day")) or index + 1 for index, day in enumerate(days)]
    if len(set(numbers)) != len(numbers):
        numbers = [index + 1 for index in range(len(days))]

    normalized = [
        normalize_day_shape(
            convert_day_text(day), day_number, oid_whitelist, product_id_whitelist
        )
        for day, day_number in zip(days, numbers)
    ]
    return dedupe_tracked_ids(normalized)


def convert_day_text(day: dict) -> dict:
    """items 文字欄位簡轉繁（風險 #3 fallback：prompt 強調仍有單字級滲漏）。"""
    items = day.get("items")
    if not isinstance(items, list):
        return day
    converted_items = []
    for item in items:
        if isinstance(item, dict):
            item = item | {
                key: to_traditional(item[key])
                for key in ("name", "text", "note", "time_band")
                if isinstance(item.get(key), str)
            }
        converted_items.append(item)
    return day | {"items": converted_items}


def normalize_messages(messages: list) -> list[dict]:
    """一輪最多 2 則、單則 ≤60 字（句尾截斷保險）；一次性生成沒有提問回合，
    type 只接受 statement/completion，其餘視為 statement。"""
    normalized = []
    for message in messages:
        if isinstance(message, dict):
            text = scalar_trim(message.get("text"))
            type_ = message.get("type", "statement")
        else:
            text = scalar_trim(message)
            type_ = "statement"
        if text == "":
            continue
        normalized.append(
            {
                "type": type_ if type_ in ("statement", "completion") else "statement",
                "text": truncate_at_sentence(to_traditional(text), MESSAGE_MAX_LENGTH),
            }
        )
    return normalized[:2] or [{"type": "completion", "text": DEFAULT_COMPLETION_MESSAGE}]


def _normalize_date_range(date_range) -> dict | None:
    """date_range 未給（沒有機票資訊）時為 null；只給局部欄位時視同未給。"""
    if not isinstance(date_range, dict) or not date_range.get("start") or not date_range.get("end"):
        return None
    return {"start": str(date_range["start"]), "end": str(date_range["end"])}


def _progress_label(days: list[dict], unplanned_days: list[int], total_days: int) -> str:
    planned = len(days) - len(unplanned_days)
    total = total_days or len(days)
    return f"已排 {planned}/{total} 天"


def _is_intish(value) -> bool:
    return isinstance(value, (int, float, str)) and not isinstance(value, bool) and _coerce_int(value) is not None


def _coerce_int(value) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
