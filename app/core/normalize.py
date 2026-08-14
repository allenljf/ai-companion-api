"""行程 day/item 正規化 + 白名單（source-spec 技法 4，逐條對照 ItineraryDayNormalizeTrait.php）。

供 travel-guide（全量生成）與 travel-revise（局部修改）共用，確保兩支 API 的行程 schema 永遠一致。
哲學：LLM 亂給就轉安全預設、絕不拋錯——normalize 後的結果才是 API 契約。

oid 與 prod_id 是兩條獨立的追蹤軸，語意刻意分離：
- oid：訂單編號（已付錢的預訂），推導 booked_anchor，revise 時不可刪除
- prod_id：商品編號（挑選要排入但尚未購買），**不影響 booked_anchor**，
  否則 App 端會在沒買的商品上誤標「已預訂」

與 PHP 版的刻意差異（詳見 tests/unit/test_normalize.py）：
- 未知欄位丟棄（PHP array_merge 會透傳）
- lat/lng 非數值 → None（PHP (float) cast 會變 0.0 的 null island）
"""

import re
from collections.abc import Collection

from pydantic import BaseModel, field_validator

TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
ITEM_TYPES = ("spot", "logistics", "meal")
TRANSPORT_MODES = ("walk", "bus", "train", "car")
DAY_STATUSES = ("planned", "discussing", "unplanned")
DAY_KINDS = ("arrival", "normal", "departure")


def scalar_trim(value) -> str:
    """非 scalar（LLM/客端亂給的陣列、物件、布林）一律視為空字串，防止 str cast 炸出 500。"""
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return ""
    return str(value).strip()


class _ItineraryItem(BaseModel):
    """欄位級強制轉型：全部用 mode="before" validator 轉安全預設，絕不 raise（CLAUDE.md 原則 2）。

    白名單與條件性 key 移除需要外部 context，在 normalize_items() 做。
    """

    name: str | None = None
    text: str = ""
    type: str = "spot"
    time_band: str | None = None
    time: str | None = None
    note: str | None = None
    lat: float | None = None
    lng: float | None = None
    transport_mode: str | None = None
    oid: str | None = None
    prod_id: str | None = None

    @field_validator("type", mode="before")
    @classmethod
    def _coerce_type(cls, v):
        return v if v in ITEM_TYPES else "spot"

    @field_validator("time", mode="before")
    @classmethod
    def _coerce_time(cls, v):
        v = v.strip() if isinstance(v, str) else ""
        return v if TIME_RE.match(v) else None

    @field_validator("transport_mode", mode="before")
    @classmethod
    def _coerce_transport(cls, v):
        v = v.strip() if isinstance(v, str) else ""
        return v if v in TRANSPORT_MODES else None

    @field_validator("lat", "lng", mode="before")
    @classmethod
    def _coerce_coord(cls, v):
        if v is None or isinstance(v, bool):
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    @field_validator("name", "oid", "prod_id", mode="before")
    @classmethod
    def _coerce_name_like(cls, v):
        return scalar_trim(v) or None

    @field_validator("text", mode="before")
    @classmethod
    def _coerce_text(cls, v):
        return scalar_trim(v)

    @field_validator("time_band", "note", mode="before")
    @classmethod
    def _coerce_optional_text(cls, v):
        return scalar_trim(v) or None


def normalize_items(
    items: list,
    allowed_oids: Collection[str] = (),
    allowed_product_ids: Collection[str] = (),
) -> list[dict]:
    """逐 item 正規化：強制轉型 → id 白名單 → 條件性 key 移除。"""
    allowed_oid_set = set(allowed_oids)
    allowed_product_set = set(allowed_product_ids)

    out = []
    for raw in items:
        item = _ItineraryItem.model_validate(raw if isinstance(raw, dict) else {}).model_dump()

        # 白名單：只認輸入帶進來的 id，幻覺一律濾成 None（排除規則明確比對集合，"0" 不被 falsy 吃掉）
        item["oid"] = item["oid"] if item["oid"] in allowed_oid_set else None
        item["prod_id"] = item["prod_id"] if item["prod_id"] in allowed_product_set else None

        # 條件性欄位：不適用的 type 整個 key 移除（不是設 None）——App 端可能用 `key in item` 判斷
        if item["type"] != "spot":
            item.pop("lat", None)
            item.pop("lng", None)
        if item["type"] != "logistics":
            item.pop("transport_mode", None)

        out.append(item)
    return out


def normalize_day_shape(
    day: dict,
    day_number: int,
    allowed_oids: Collection[str] = (),
    allowed_product_ids: Collection[str] = (),
) -> dict:
    """單天正規化；day 編號由呼叫端決定（guide 用陣列索引、revise 重編 1..N），本函式不自行推斷。"""
    day = day if isinstance(day, dict) else {}
    items = day.get("items")

    status = day.get("status", "planned")
    kind = day.get("kind", "normal")

    normalized_items = normalize_items(
        items if isinstance(items, list) else [], allowed_oids, allowed_product_ids
    )

    return {
        "day": day_number,
        "status": status if status in DAY_STATUSES else "planned",
        "kind": kind if kind in DAY_KINDS else "normal",
        "half_day": bool(day.get("half_day", False)),
        "items": normalized_items,
        # 一律不採信模型輸出，由後端依該天通過白名單的 oid 推導（prod_id 刻意不算）
        "booked_anchor": derive_booked_anchor(normalized_items),
    }


def derive_booked_anchor(items: list[dict]) -> dict | None:
    """該天有通過白名單的 oid 才有錨點 {"oids": [...]}；沒有一律 None（App 依 null 與否判斷）。"""
    seen: dict[str, None] = {}
    for item in items:
        oid = item.get("oid")
        if oid is not None and oid != "":
            seen.setdefault(oid, None)
    return {"oids": list(seen)} if seen else None


def dedupe_tracked_ids(days: list[dict]) -> list[dict]:
    """同一 oid／prod_id 全行程只認第一次出現，後續轉 None 並重推該天 booked_anchor。

    LLM 把一筆訂單/商品複製到多 item／多天時（prompt 已禁止但不可信任），重複項若不清掉，
    會在 revise 迴圈被「不可刪除」規則固化。兩個欄位各自獨立計數。
    """
    used: dict[str, set] = {"oid": set(), "prod_id": set()}
    for day in days:
        for item in day["items"]:
            for key, seen in used.items():
                value = item.get(key)
                if value is None:
                    continue
                if value in seen:
                    item[key] = None
                else:
                    seen.add(value)
        day["booked_anchor"] = derive_booked_anchor(day["items"])
    return days
