"""normalize 層測試（migration-plan 階段 4 測試清單，逐條對照 ItineraryDayNormalizeTrait.php）。

哲學：LLM 亂給就轉安全預設、絕不拋錯（source-spec 技法 4）。
與 PHP 的刻意差異（各有測試標註）：
- 未知欄位丟棄（PHP array_merge 會留下）——normalize 後的形狀就是契約，不透傳垃圾 key
- lat/lng 非數值 → None（PHP (float) cast 會變 0.0 的 null island）
"""

from app.core.normalize import (
    dedupe_tracked_ids,
    derive_booked_anchor,
    normalize_day_shape,
    normalize_items,
)

# ---------------------------------------------------------------------------
# normalize_items：欄位預設值與強制轉型
# ---------------------------------------------------------------------------


def test_missing_fields_get_defaults():
    [item] = normalize_items([{}])
    assert item == {
        "name": None,
        "text": "",
        "type": "spot",
        "time_band": None,
        "time": None,
        "note": None,
        "lat": None,
        "lng": None,
        "oid": None,
        "prod_id": None,
    }


def test_unknown_keys_are_dropped():
    # 刻意與 PHP 不同：LLM 多給的欄位（如捏造的 price）不透傳
    [item] = normalize_items([{"name": "清水寺", "price": "NT$300", "foo": 1}])
    assert "price" not in item and "foo" not in item


def test_type_outside_enum_falls_back_to_spot():
    items = normalize_items([{"type": "hotel"}, {"type": None}, {"type": 3}])
    assert [i["type"] for i in items] == ["spot", "spot", "spot"]


def test_valid_types_kept():
    items = normalize_items([{"type": "spot"}, {"type": "logistics"}, {"type": "meal"}])
    assert [i["type"] for i in items] == ["spot", "logistics", "meal"]


def test_time_invalid_formats_become_none():
    bad = ["9:00", "25:99", None, "09:00:00", "9 AM", "24:00", 900, ["09:00"]]
    items = normalize_items([{"time": t} for t in bad])
    assert all(i["time"] is None for i in items)


def test_time_valid_kept_and_trimmed():
    items = normalize_items([{"time": "09:30"}, {"time": " 23:59 "}, {"time": "00:00"}])
    assert [i["time"] for i in items] == ["09:30", "23:59", "00:00"]


def test_transport_mode_outside_enum_becomes_none():
    bad = ["rocket", "WALK", "", None, 5, ["walk"]]
    items = normalize_items([{"type": "logistics", "transport_mode": m} for m in bad])
    assert all(i["transport_mode"] is None for i in items)


def test_transport_mode_valid_kept():
    items = normalize_items(
        [{"type": "logistics", "transport_mode": m} for m in ("walk", "bus", "train", "car")]
    )
    assert [i["transport_mode"] for i in items] == ["walk", "bus", "train", "car"]


# ---------------------------------------------------------------------------
# 條件性欄位：key 移除（不是設 None）——App 端可能用 `key in item` 判斷
# ---------------------------------------------------------------------------


def test_non_spot_items_have_no_lat_lng_keys():
    items = normalize_items(
        [
            {"type": "logistics", "lat": 35.0, "lng": 135.0},
            {"type": "meal", "lat": 35.0, "lng": 135.0},
        ]
    )
    for item in items:
        assert "lat" not in item and "lng" not in item


def test_spot_keeps_lat_lng_keys_even_when_none():
    [item] = normalize_items([{"type": "spot"}])
    assert "lat" in item and "lng" in item
    assert item["lat"] is None and item["lng"] is None


def test_non_logistics_items_have_no_transport_mode_key():
    items = normalize_items([{"type": "spot"}, {"type": "meal", "transport_mode": "walk"}])
    for item in items:
        assert "transport_mode" not in item


def test_logistics_keeps_transport_mode_key_even_when_none():
    [item] = normalize_items([{"type": "logistics"}])
    assert "transport_mode" in item and item["transport_mode"] is None


def test_lat_lng_numeric_strings_coerced_to_float():
    [item] = normalize_items([{"type": "spot", "lat": "35.01", "lng": 135}])
    assert item["lat"] == 35.01 and item["lng"] == 135.0
    assert isinstance(item["lat"], float) and isinstance(item["lng"], float)


def test_lat_lng_garbage_becomes_none_not_zero():
    # 刻意與 PHP 不同：(float)"abc" 在 PHP 是 0.0（null island），這裡寧可回 None
    [item] = normalize_items([{"type": "spot", "lat": "abc", "lng": ["135"]}])
    assert item["lat"] is None and item["lng"] is None


# ---------------------------------------------------------------------------
# 白名單：oid / prod_id 只認輸入帶進來的，幻覺一律 None
# ---------------------------------------------------------------------------


def test_oid_not_in_whitelist_becomes_none():
    [item] = normalize_items([{"oid": "FAKE123"}], allowed_oids={"REAL1"})
    assert item["oid"] is None


def test_oid_in_whitelist_kept():
    [item] = normalize_items([{"oid": " REAL1 "}], allowed_oids={"REAL1"})
    assert item["oid"] == "REAL1"


def test_oid_zero_string_is_not_eaten_by_falsy_rules():
    [item] = normalize_items([{"oid": "0"}], allowed_oids={"0"})
    assert item["oid"] == "0"


def test_prod_id_not_in_whitelist_becomes_none():
    [item] = normalize_items([{"prod_id": "FAKE"}], allowed_product_ids={"P1"})
    assert item["prod_id"] is None


def test_prod_id_whitelist_independent_from_oid_whitelist():
    # 同字串值出現在另一條軸的白名單也不算數——兩條追蹤軸各自獨立
    [item] = normalize_items(
        [{"oid": "X1", "prod_id": "X1"}], allowed_oids={"X1"}, allowed_product_ids=set()
    )
    assert item["oid"] == "X1" and item["prod_id"] is None


def test_empty_whitelists_null_all_ids():
    [item] = normalize_items([{"oid": "A", "prod_id": "B"}])
    assert item["oid"] is None and item["prod_id"] is None


def test_non_scalar_oid_and_prod_id_do_not_crash():
    # LLM/客端亂給陣列、物件、布林 → 一律視同沒帶（PHP 版是防 Array to string 500）
    weird = [{"oid": ["A"], "prod_id": {"x": 1}}, {"oid": True, "prod_id": False}]
    items = normalize_items(weird, allowed_oids={"A", "1"}, allowed_product_ids={"1"})
    for item in items:
        assert item["oid"] is None and item["prod_id"] is None


# ---------------------------------------------------------------------------
# name：空字串視同沒填
# ---------------------------------------------------------------------------


def test_name_empty_or_blank_becomes_none():
    items = normalize_items([{"name": ""}, {"name": "   "}, {"name": ["清水寺"]}])
    assert all(i["name"] is None for i in items)


def test_name_trimmed():
    [item] = normalize_items([{"name": " 清水寺 "}])
    assert item["name"] == "清水寺"


def test_name_numeric_scalar_stringified():
    [item] = normalize_items([{"name": 33}])
    assert item["name"] == "33"


# ---------------------------------------------------------------------------
# 其他欄位與 item 本身的非 scalar 防炸
# ---------------------------------------------------------------------------


def test_text_non_scalar_becomes_empty_and_scalar_stringified():
    items = normalize_items([{"text": ["x"]}, {"text": 123}])
    assert items[0]["text"] == "" and items[1]["text"] == "123"


def test_time_band_and_note_non_scalar_become_none():
    [item] = normalize_items([{"time_band": ["上午"], "note": {"x": 1}}])
    assert item["time_band"] is None and item["note"] is None


def test_non_dict_items_treated_as_empty():
    items = normalize_items(["oops", None, 5])
    assert len(items) == 3
    assert all(i["type"] == "spot" for i in items)


# ---------------------------------------------------------------------------
# normalize_day_shape：day 骨架與編號
# ---------------------------------------------------------------------------


def test_day_shape_defaults():
    day = normalize_day_shape({}, 3)
    assert day == {
        "day": 3,
        "status": "planned",
        "kind": "normal",
        "half_day": False,
        "items": [],
        "booked_anchor": None,
    }


def test_day_number_is_caller_assigned_not_model_output():
    # day 編號由呼叫端決定（本函式不自行推斷）；模型輸出的 day 欄位不採信
    day = normalize_day_shape({"day": 99}, 2)
    assert day["day"] == 2


def test_day_status_and_kind_outside_enum_fall_back():
    day = normalize_day_shape({"status": "maybe", "kind": "rest"}, 1)
    assert day["status"] == "planned" and day["kind"] == "normal"


def test_day_valid_status_and_kind_kept():
    day = normalize_day_shape({"status": "unplanned", "kind": "departure"}, 5)
    assert day["status"] == "unplanned" and day["kind"] == "departure"


def test_day_half_day_coerced_to_bool():
    assert normalize_day_shape({"half_day": 1}, 1)["half_day"] is True
    assert normalize_day_shape({"half_day": 0}, 1)["half_day"] is False
    assert normalize_day_shape({"half_day": "yes"}, 1)["half_day"] is True


def test_day_items_non_list_becomes_empty():
    assert normalize_day_shape({"items": None}, 1)["items"] == []
    assert normalize_day_shape({"items": "oops"}, 1)["items"] == []


# ---------------------------------------------------------------------------
# booked_anchor：只由通過白名單的 oid 推導，prod_id 刻意不算
# ---------------------------------------------------------------------------


def test_booked_anchor_derived_from_whitelisted_oids():
    day = normalize_day_shape(
        {"items": [{"oid": "O1"}, {"oid": "FAKE"}, {"oid": "O2"}]},
        1,
        allowed_oids={"O1", "O2"},
    )
    assert day["booked_anchor"] == {"oids": ["O1", "O2"]}


def test_booked_anchor_none_without_oids():
    day = normalize_day_shape({"items": [{"name": "清水寺"}]}, 1)
    assert day["booked_anchor"] is None


def test_booked_anchor_not_affected_by_prod_id():
    day = normalize_day_shape(
        {"items": [{"prod_id": "P1"}]}, 1, allowed_product_ids={"P1"}
    )
    assert day["items"][0]["prod_id"] == "P1"
    assert day["booked_anchor"] is None


def test_booked_anchor_ignores_model_supplied_value():
    # 一律不採信模型輸出的 booked_anchor
    day = normalize_day_shape({"booked_anchor": {"oids": ["HALLUCINATED"]}}, 1)
    assert day["booked_anchor"] is None


def test_derive_booked_anchor_dedupes_and_keeps_order():
    items = [{"oid": "A"}, {"oid": None}, {"oid": "B"}, {"oid": "A"}]
    assert derive_booked_anchor(items) == {"oids": ["A", "B"]}


def test_derive_booked_anchor_keeps_zero_string():
    assert derive_booked_anchor([{"oid": "0"}]) == {"oids": ["0"]}


# ---------------------------------------------------------------------------
# dedupe_tracked_ids：同 id 全行程只認第一次（oid / prod_id 各自獨立計數）
# ---------------------------------------------------------------------------


def _days_with(items_per_day: list[list[dict]], **whitelists) -> list[dict]:
    return [
        normalize_day_shape({"items": items}, i + 1, **whitelists)
        for i, items in enumerate(items_per_day)
    ]


def test_same_oid_across_days_only_first_kept():
    days = _days_with(
        [[{"oid": "O1"}], [{"oid": "O1"}, {"name": "x"}]], allowed_oids={"O1"}
    )
    days = dedupe_tracked_ids(days)
    assert days[0]["items"][0]["oid"] == "O1"
    assert days[1]["items"][0]["oid"] is None
    # 去重後 booked_anchor 要重推，否則重複天還掛著錨點
    assert days[0]["booked_anchor"] == {"oids": ["O1"]}
    assert days[1]["booked_anchor"] is None


def test_same_oid_twice_within_one_day_only_first_kept():
    days = dedupe_tracked_ids(
        _days_with([[{"oid": "O1"}, {"oid": "O1"}]], allowed_oids={"O1"})
    )
    oids = [item["oid"] for item in days[0]["items"]]
    assert oids == ["O1", None]
    assert days[0]["booked_anchor"] == {"oids": ["O1"]}


def test_same_prod_id_across_days_only_first_kept():
    days = dedupe_tracked_ids(
        _days_with([[{"prod_id": "P1"}], [{"prod_id": "P1"}]], allowed_product_ids={"P1"})
    )
    assert days[0]["items"][0]["prod_id"] == "P1"
    assert days[1]["items"][0]["prod_id"] is None


def test_oid_and_prod_id_dedupe_counters_are_independent():
    # 同字串值 "X" 同時當 oid 與 prod_id：兩軸各自都算「第一次」，互不干擾
    days = dedupe_tracked_ids(
        _days_with(
            [[{"oid": "X"}], [{"prod_id": "X"}]],
            allowed_oids={"X"},
            allowed_product_ids={"X"},
        )
    )
    assert days[0]["items"][0]["oid"] == "X"
    assert days[1]["items"][0]["prod_id"] == "X"


def test_dedupe_preserves_day_count_and_shape():
    days = dedupe_tracked_ids(_days_with([[], [], []]))
    assert [d["day"] for d in days] == [1, 2, 3]
