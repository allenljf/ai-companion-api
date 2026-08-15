"""TravelIdentityMapper 八人格判定測試（逐條對照 TravelIdentityMapper.php + 規格第 3 節）。

判定不經 LLM：第 1 層 interest valence × stimulation axis 定象限（A/B/C/D），
第 2 層 social_company / social_local 定子類型（1 內向 / 2 外向）。
environment / pace / spending 為輔助標籤，不參與判定。
"""

import json
from pathlib import Path

import pytest

from app.services.companion.identity_mapper import map_travel_identity

DIMENSIONS = json.loads(Path("data/quiz_dimensions.json").read_text())["dimensions"]

# 真實 tag id 對照（data/quiz_dimensions.json）：
# t1-1 資深吃貨(thrill)  t1-2 身心療癒派(nourish)  t1-3 文青魂上身(nourish)
# t1-4 歡樂派對咖(thrill) t1-5 戰利品收藏家(thrill)
# t2-1 歷史文化迷(nourish) t2-2 異國體驗派(thrill*) t2-3 戶外挑戰咖(thrill*) t2-4 大自然充電族(nourish)
#   (* = novel-leaning)
# t5-1 安心舒適圈(familiar) t5-2 偶爾想試點新的(neutral) t5-3 越冷門越想衝(novel)
# t7-1 一個人最自在(introvert) t7-2 兩個人剛剛好(introvert) t7-3 一群人才好玩(extrovert)
# t8-1 安靜觀察家(introvert) t8-2 看心情社交(introvert) t8-3 走到哪聊到哪(extrovert)


def m(tags: list[str]) -> dict:
    return map_travel_identity(tags, DIMENSIONS)


class TestEightPersonas:
    def test_a1_solo_healer(self):
        # nourish × familiar × introvert
        result = m(["t1-2", "t2-4", "t5-1", "t7-1", "t8-1"])
        assert result["persona_id"] == "A1"
        assert result["travel_identity"] == "獨處療癒師"
        assert result["travel_identity_en"] == "Solo Healer"

    def test_a2_group_vacationer(self):
        result = m(["t1-2", "t2-4", "t5-1", "t7-3", "t8-3"])
        assert result["persona_id"] == "A2"
        assert result["travel_identity"] == "揪團度假派"

    def test_b1_alley_wanderer(self):
        # nourish × novel × introvert
        result = m(["t1-3", "t2-1", "t5-3", "t7-1", "t8-2"])
        assert result["persona_id"] == "B1"
        assert result["travel_identity"] == "巷弄獨行客"

    def test_b2_global_culture_hopper(self):
        result = m(["t1-3", "t2-1", "t5-3", "t7-3", "t8-3"])
        assert result["persona_id"] == "B2"
        assert result["travel_identity"] == "異國走跳咖"

    def test_c1_hidden_gem_connoisseur(self):
        # thrill × familiar × introvert
        result = m(["t1-1", "t1-4", "t5-1", "t7-2", "t8-1"])
        assert result["persona_id"] == "C1"
        assert result["travel_identity"] == "私房鑑賞家"

    def test_c2_party_crew_player(self):
        result = m(["t1-1", "t1-4", "t5-1", "t7-3", "t8-3"])
        assert result["persona_id"] == "C2"
        assert result["travel_identity"] == "嗨咖玩家派"

    def test_d1_lone_grand_adventurer(self):
        # thrill × novel × introvert
        result = m(["t1-1", "t2-3", "t5-3", "t7-1", "t8-1"])
        assert result["persona_id"] == "D1"
        assert result["travel_identity"] == "孤獨壯遊者"

    def test_d2_expedition_adventure_crew(self):
        result = m(["t1-1", "t2-3", "t5-3", "t7-3", "t8-3"])
        assert result["persona_id"] == "D2"
        assert result["travel_identity"] == "遠征冒險團"


class TestValenceTieBreak:
    def test_1v1_tie_familiar_leans_nourish(self):
        # 興趣 1:1（療癒 nourish + 吃貨 thrill... 注意 t1 只會選 1 個，用 t1+t2 湊 1:1）
        result = m(["t1-2", "t2-3", "t5-1", "t7-1"])  # nourish + thrill, familiar
        assert result["persona_id"][0] == "A"  # familiar 破平手 → nourish

    def test_1v1_tie_novel_leans_thrill(self):
        result = m(["t1-2", "t2-3", "t5-3", "t7-1"])  # nourish + thrill, novel
        assert result["persona_id"][0] == "D"  # novel 破平手 → thrill

    def test_1v1_tie_neutral_takes_first_interest_by_dimension_order(self):
        # neutral：題序優先取興趣第 1 題（dimension id 最小）的 valence。
        # 傳入順序刻意反過來（t2 在前），驗證是依 dimension id 排序不是傳入順序
        result = m(["t2-3", "t1-2", "t5-2", "t7-1"])
        # dim1=身心療癒派(nourish) 在 dim2=戶外挑戰咖(thrill) 前 → nourish；
        # 但 stimulation neutral + 含 novel-leaning 興趣（戶外挑戰咖）→ novel → B
        assert result["persona_id"][0] == "B"

    def test_no_interests_defaults_nourish(self):
        result = m(["t5-1", "t7-1"])
        assert result["persona_id"][0] == "A"


class TestStimulationAxis:
    def test_neutral_with_novel_leaning_interest_becomes_novel(self):
        # 偶爾想試點新的（neutral）+ 異國體驗派（novel-leaning）→ novel
        result = m(["t1-1", "t2-2", "t5-2", "t7-1"])
        assert result["persona_id"][0] == "D"  # thrill × novel

    def test_neutral_without_novel_leaning_defaults_familiar(self):
        result = m(["t1-1", "t1-4", "t5-2", "t7-1"])  # 吃貨+派對咖，皆非 novel-leaning
        assert result["persona_id"][0] == "C"  # thrill × familiar

    def test_missing_stimulation_defaults_familiar(self):
        result = m(["t1-2", "t2-4", "t7-1"])
        assert result["persona_id"][0] == "A"


class TestSubtype:
    def test_company_beats_local_when_different(self):
        # company 外向 + local 內向 → 以 company 為準 → 外向
        result = m(["t1-2", "t2-4", "t5-1", "t7-3", "t8-1"])
        assert result["persona_id"][1] == "2"

        # company 內向 + local 外向 → 內向
        result = m(["t1-2", "t2-4", "t5-1", "t7-1", "t8-3"])
        assert result["persona_id"][1] == "1"

    def test_company_missing_falls_back_to_local(self):
        result = m(["t1-2", "t2-4", "t5-1", "t8-3"])
        assert result["persona_id"][1] == "2"

    def test_both_missing_defaults_introvert(self):
        result = m(["t1-2", "t2-4", "t5-1"])
        assert result["persona_id"][1] == "1"

    def test_two_person_preference_is_introvert(self):
        # 兩個人剛剛好 → 內向（規格如此）
        result = m(["t1-2", "t2-4", "t5-1", "t7-2"])
        assert result["persona_id"][1] == "1"


class TestRobustness:
    def test_unknown_tag_id_treated_as_label(self):
        # 查無 ID 時視為 label 容錯：直接傳中文 label 也能判定
        result = m(["資深吃貨", "越冷門越想衝", "一群人才好玩"])
        assert result["persona_id"] == "D2"

    def test_auxiliary_tags_do_not_affect_result(self):
        # environment(t6)/pace(t4)/spending(t3) 為輔助標籤，不參與判定
        base = m(["t1-2", "t2-4", "t5-1", "t7-1"])
        with_aux = m(["t1-2", "t2-4", "t5-1", "t7-1", "t3-1", "t4-3", "t6-4"])
        assert base == with_aux

    def test_first_answer_wins_within_axis(self):
        # 同軸出現兩個答案時只認第一個（?? 語意）
        result = m(["t1-2", "t2-4", "t5-1", "t5-3", "t7-1"])
        assert result["persona_id"][0] == "A"  # familiar（第一個）而非 novel

    def test_empty_input_gets_default_persona(self):
        result = m([])
        assert result["persona_id"] == "A1"  # nourish 預設 × familiar 預設 × 內向預設

    def test_result_shape(self):
        result = m(["t1-1"])
        assert set(result) == {"persona_id", "travel_identity", "travel_identity_en"}
