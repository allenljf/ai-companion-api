"""share-image-v2 三層 prompt 組裝 + fallback 分類測試（source-spec 4.4 / 8.5）。"""

import pytest

from app.services.companion.share_fallback import hero_fallback_category, tag_fallback_category
from app.services.companion.share_prompts import (
    HERO_HARD_CONSTRAINTS,
    STAMP_HARD_CONSTRAINTS,
    TAG_HARD_CONSTRAINTS,
    build_hero_prompt,
    build_stamp_prompt,
    build_tag_prompt,
)

ANALYSIS = {
    "travel_identity": "獨處療癒師",
    "destination_cn": "京都",
    "destination_en": "Kyoto",
    "highlight_tags": ["身心療癒派", "大自然充電族", "安心舒適圈"],
}


class TestHeroPrompt:
    def test_three_layers_in_order(self):
        prompt = build_hero_prompt(ANALYSIS)
        # 第 1 層 art_style（DCS anime 版）→ 第 2 層主體 → 第 3 層硬約束（最後、含否決權）
        art_pos = prompt.index("Anime-style illustration")
        scene_pos = prompt.index("travel personality")
        constraint_pos = prompt.index(HERO_HARD_CONSTRAINTS)
        assert art_pos < scene_pos < constraint_pos
        assert prompt.rstrip().endswith(HERO_HARD_CONSTRAINTS)

    def test_placeholders_substituted(self):
        prompt = build_hero_prompt(ANALYSIS)
        assert '"獨處療癒師"' in prompt
        assert '"Kyoto"' in prompt
        assert "{travel_identity}" not in prompt and "{destination_en}" not in prompt

    def test_no_text_constraint_present(self):
        assert "NO text" in build_hero_prompt(ANALYSIS)


class TestStampPrompt:
    def test_destination_prefers_english(self):
        prompt = build_stamp_prompt(ANALYSIS)
        assert '"Kyoto"' in prompt

    def test_destination_falls_back_to_cn(self):
        prompt = build_stamp_prompt(ANALYSIS | {"destination_en": ""})
        assert '"京都"' in prompt

    def test_dark_background_constraint_last(self):
        prompt = build_stamp_prompt(ANALYSIS)
        assert prompt.rstrip().endswith(STAMP_HARD_CONSTRAINTS)
        assert "#1A1A1A" in prompt


class TestTagPrompt:
    def test_tag_substituted_by_index(self):
        prompt = build_tag_prompt(ANALYSIS, 1)
        assert '"大自然充電族"' in prompt
        assert prompt.rstrip().endswith(TAG_HARD_CONSTRAINTS)
        assert "#FFFFFF" in prompt

    def test_invalid_index_raises(self):
        with pytest.raises(IndexError):
            build_tag_prompt(ANALYSIS, 3)


class TestFallbackCategories:
    def test_hero_categories(self):
        assert hero_fallback_category({"destination_cn": "京都", "destination_en": "Kyoto"}) == "culture"
        assert hero_fallback_category({"destination_cn": "東京", "destination_en": ""}) == "urban"
        assert hero_fallback_category({"destination_cn": "", "destination_en": "Queenstown"}) == "mountain"
        assert hero_fallback_category({"destination_cn": "火星", "destination_en": "Mars"}) == "generic"

    def test_hero_case_insensitive(self):
        assert hero_fallback_category({"destination_en": "TOKYO"}) == "urban"

    def test_tag_categories(self):
        assert tag_fallback_category("資深吃貨") == "food"
        assert tag_fallback_category("戶外挑戰咖") == "adventure"
        assert tag_fallback_category("身心療癒派") == "relaxation"
        assert tag_fallback_category("文青魂上身") == "culture"
        assert tag_fallback_category("戰利品收藏家") == "shopping"
        assert tag_fallback_category("夜貓子") == "nightlife"
        assert tag_fallback_category("完全不相關") == "generic"

    def test_declaration_order_first_match_wins(self):
        # 「吃」在 food、「文化」在 culture——同時命中取宣告順序第一個（food 在前）
        assert tag_fallback_category("吃文化") == "food"
