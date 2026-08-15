"""依「旅遊人格測驗實作規格」將答題標籤 mapping 為八種旅行人格之一（逐條對照 TravelIdentityMapper.php）。

判定不經過 LLM，一律走規格第 3 節計分邏輯：
第 1 層以 interest valence × stimulation axis 定象限（A/B/C/D），
第 2 層以 social_company / social_local 定子類型（1 內向 / 2 外向）。
environment / pace / spending 為輔助標籤，不參與判定。

標籤以「中文 label」對照（規格 1.2 標籤總表），tag ID（t1-1…）由
quiz dimensions 解析成 label 後再分類，label 是與規格文件的穩定契約。
"""

# interest 標籤 → valence（nourish 滋養 / thrill 刺激）
INTEREST_VALENCE = {
    "歷史文化迷": "nourish",
    "文青魂上身": "nourish",
    "大自然充電族": "nourish",
    "身心療癒派": "nourish",
    "資深吃貨": "thrill",
    "歡樂派對咖": "thrill",
    "戰利品收藏家": "thrill",
    "戶外挑戰咖": "thrill",
    "異國體驗派": "thrill",
}

# stimulation 標籤 → 熟悉/新鮮光譜
STIMULATION_AXIS = {
    "安心舒適圈": "familiar",
    "偶爾想試點新的": "neutral",
    "越冷門越想衝": "novel",
}

# social_company 標籤 → 內向/外向
COMPANY_AXIS = {
    "一個人最自在": "introvert",
    "兩個人剛剛好": "introvert",
    "一群人才好玩": "extrovert",
}

# social_local 標籤 → 內向/外向
LOCAL_AXIS = {
    "安靜觀察家": "introvert",
    "看心情社交": "introvert",
    "走到哪聊到哪": "extrovert",
}

# stimulation 為 neutral 時借力興趣標籤傾向（規格 3.2 Step 2）
NOVEL_LEANING_INTERESTS = ("戶外挑戰咖", "異國體驗派")

# 八人格核心標籤對照（規格 2.2）；英文名為暫定翻譯，規格未定義
PERSONAS = {
    "A1": {"travel_identity": "獨處療癒師", "travel_identity_en": "Solo Healer"},
    "A2": {"travel_identity": "揪團度假派", "travel_identity_en": "Group Vacationer"},
    "B1": {"travel_identity": "巷弄獨行客", "travel_identity_en": "Alley Wanderer"},
    "B2": {"travel_identity": "異國走跳咖", "travel_identity_en": "Global Culture Hopper"},
    "C1": {"travel_identity": "私房鑑賞家", "travel_identity_en": "Hidden Gem Connoisseur"},
    "C2": {"travel_identity": "嗨咖玩家派", "travel_identity_en": "Party Crew Player"},
    "D1": {"travel_identity": "孤獨壯遊者", "travel_identity_en": "Lone Grand Adventurer"},
    "D2": {"travel_identity": "遠征冒險團", "travel_identity_en": "Expedition Adventure Crew"},
}

_QUADRANTS = {
    ("nourish", "familiar"): "A",
    ("nourish", "novel"): "B",
    ("thrill", "familiar"): "C",
    ("thrill", "novel"): "D",
}


def map_travel_identity(selected_tag_ids: list, dimensions: list[dict]) -> dict:
    """答題 tag ID（如 t1-1；查無 ID 時視為 label 容錯）→ 八人格之一。"""
    answers = _classify_answers(selected_tag_ids, dimensions)
    persona_id = _resolve_quadrant(answers) + _resolve_subtype(answers)
    return {"persona_id": persona_id} | PERSONAS[persona_id]


def _classify_answers(selected_tag_ids: list, dimensions: list[dict]) -> dict:
    """tag ID 解析為 label 後分到各判定維度；interests 依 dimension id 升冪（題序優先破平手用）。"""
    tag_map: dict[str, dict] = {}
    for dimension in dimensions:
        for tag in dimension.get("tags") or []:
            tag_map[str(tag.get("id", ""))] = {
                "dimension_id": int(dimension.get("id") or 2**31),
                "label": str(tag.get("label", "")),
            }

    interests: list[dict] = []
    answers: dict = {"stimulation": None, "company": None, "local": None}
    for tag_id in selected_tag_ids:
        info = tag_map.get(str(tag_id))
        label = info["label"] if info else str(tag_id)

        if label in INTEREST_VALENCE:
            interests.append(
                {"dimension_id": info["dimension_id"] if info else 2**31, "label": label}
            )
        elif label in STIMULATION_AXIS:
            answers["stimulation"] = answers["stimulation"] or STIMULATION_AXIS[label]
        elif label in COMPANY_AXIS:
            answers["company"] = answers["company"] or COMPANY_AXIS[label]
        elif label in LOCAL_AXIS:
            answers["local"] = answers["local"] or LOCAL_AXIS[label]
        # environment / pace / spending 為輔助標籤，不參與判定，直接略過

    interests.sort(key=lambda item: item["dimension_id"])
    answers["interests"] = [item["label"] for item in interests]
    return answers


def _resolve_quadrant(answers: dict) -> str:
    return _QUADRANTS[(_resolve_valence(answers), _resolve_stimulation_axis(answers))]


def _resolve_valence(answers: dict) -> str:
    """Step 1：判 interest valence；1:1 騎牆時以 stimulation 破平手，
    neutral 則題序優先取興趣第 1 題的 valence（無興趣答案時預設 nourish）。"""
    valences = [INTEREST_VALENCE[label] for label in answers["interests"]]
    nourish = valences.count("nourish")
    thrill = len(valences) - nourish

    if nourish != thrill:
        return "nourish" if nourish > thrill else "thrill"
    if answers["stimulation"] == "familiar":
        return "nourish"
    if answers["stimulation"] == "novel":
        return "thrill"
    first = answers["interests"][0] if answers["interests"] else ""
    return INTEREST_VALENCE.get(first, "nourish")


def _resolve_stimulation_axis(answers: dict) -> str:
    """Step 2：neutral（或未作答）時借力興趣標籤傾向——
    含 novel 傾向興趣（戶外挑戰咖/異國體驗派）→ novel，否則預設 familiar。"""
    if answers["stimulation"] in ("familiar", "novel"):
        return answers["stimulation"]
    if set(NOVEL_LEANING_INTERESTS) & set(answers["interests"]):
        return "novel"
    return "familiar"


def _resolve_subtype(answers: dict) -> str:
    """第 2 層：company 權重 > local，company 未作答退回 local，皆缺預設內向（子類型 1）。"""
    axis = answers["company"] or answers["local"] or "introvert"
    return "1" if axis == "introvert" else "2"
