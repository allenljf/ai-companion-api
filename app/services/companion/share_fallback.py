"""海報 fallback 素材分類（對照 PosterFallbackCategoryResolver.php）。

hero：依 destination 判定六種背景類別；tag：依關鍵字判定七種主題類別。
皆以宣告順序取第一個命中，無命中回 'generic'。App 端在對應素材產圖失敗時用這個分類挑本地兜底圖。
"""

HERO_CITIES = {
    "urban": ["大阪", "東京", "首爾", "曼谷", "台北", "新加坡", "osaka", "tokyo", "seoul", "bangkok", "taipei", "singapore"],
    "coast": ["卑爾根", "釜山", "里斯本", "巴塞隆納", "雪梨", "bergen", "busan", "lisbon", "barcelona", "sydney"],
    "mountain": ["策馬特", "因特拉肯", "班夫", "皇后鎮", "zermatt", "interlaken", "banff", "queenstown"],
    "nature": ["札幌", "雷克雅維克", "特羅姆瑟", "羅瓦涅米", "sapporo", "reykjavik", "tromso", "tromsø", "rovaniemi"],
    "culture": ["京都", "羅馬", "佛羅倫斯", "布拉格", "伊斯坦堡", "kyoto", "rome", "florence", "prague", "istanbul"],
}

TAG_WORDS = {
    "food": ["吃", "食", "料理", "咖啡", "甜點"],
    "adventure": ["冒險", "戶外", "挑戰", "健行", "運動"],
    "relaxation": ["放空", "療癒", "慢活", "悠閒", "按摩"],
    "culture": ["文化", "歷史", "藝術", "文青", "博物館"],
    "shopping": ["購物", "逛街", "戰利品", "時尚"],
    "nightlife": ["夜貓", "夜生活", "酒吧", "派對"],
}


def hero_fallback_category(analysis: dict) -> str:
    combined = (
        str(analysis.get("destination_cn") or "").strip()
        + " "
        + str(analysis.get("destination_en") or "").strip()
    ).lower()
    return _first_match(combined, HERO_CITIES)


def tag_fallback_category(tag: str) -> str:
    return _first_match(tag.strip().lower(), TAG_WORDS)


def _first_match(value: str, groups: dict[str, list[str]]) -> str:
    for category, keywords in groups.items():
        if any(keyword in value for keyword in keywords):
            return category
    return "generic"
