"""travel-summary 的 persona / user message 純函式（對照 reference TravelSummaryService.php）。"""

import json

from app.services.plan.summary import (
    FIXED_WELCOME_TEMPLATE,
    build_import_user_text,
    build_order_pick_user_message,
    build_quiz_user_message,
    persona_text,
)


class TestPersonaText:
    def test_default_name_when_empty(self):
        assert persona_text({}) == "你的名字是「小旅」。"

    def test_name_only(self):
        assert persona_text({"companion_name": "阿旅"}) == "你的名字是「阿旅」。"

    def test_blank_name_falls_back(self):
        assert persona_text({"companion_name": "   "}) == "你的名字是「小旅」。"

    def test_full_traits(self):
        out = persona_text(
            {"companion_name": "阿旅", "personality": "幽默", "speech_style": "親切"}
        )
        assert out == "你的名字是「阿旅」，人格特質與說話風格關鍵字：幽默、親切，請以此語氣說話。"

    def test_single_trait(self):
        out = persona_text({"companion_name": "阿旅", "personality": "幽默"})
        assert out == "你的名字是「阿旅」，人格特質與說話風格關鍵字：幽默，請以此語氣說話。"


def _extract_payload(msg: str) -> dict:
    assert msg.startswith("<<<USER_INPUT\n")
    assert msg.endswith("\n>>>END_USER_INPUT")
    return json.loads(msg[len("<<<USER_INPUT\n") : -len("\n>>>END_USER_INPUT")])


class TestUserMessages:
    def test_quiz_payload_fields(self):
        msg = build_quiz_user_message(
            {
                "city": " 大阪 ",
                "city_image_url": "https://x/img.png",
                "intro_text": "介紹",
                "previous_summary": "上次",
                "note": "補充",
            }
        )
        payload = _extract_payload(msg)
        assert payload == {
            "city": "大阪",
            "city_image_url": "https://x/img.png",
            "intro_text": "介紹",
            "previous_summary": "上次",
            "note": "補充",
        }
        # 中文不可被 escape 成 \uXXXX（對應 PHP JSON_UNESCAPED_UNICODE）
        assert "大阪" in msg

    def test_quiz_payload_defaults_to_empty_strings(self):
        payload = _extract_payload(build_quiz_user_message({"city": "大阪"}))
        assert payload["intro_text"] == ""
        assert payload["previous_summary"] == ""

    def test_order_pick_payload_shape(self):
        msg = build_order_pick_user_message(
            {
                "city": "東京",
                "order": {"prod_name": "門票", "package_name": "1日券", "go_dt": "2026-09-02"},
            }
        )
        payload = _extract_payload(msg)
        assert payload["city"] == "東京"
        assert payload["order"] == {
            "prod_name": "門票",
            "package_name": "1日券",
            "go_dt": "2026-09-02",
        }

    def test_order_pick_without_order(self):
        payload = _extract_payload(build_order_pick_user_message({"city": "東京"}))
        assert payload["order"] == {"prod_name": "", "package_name": "", "go_dt": ""}

    def test_import_text_payload(self):
        msg = build_import_user_text(
            {"source_type": "text", "content": "Day 1 清水寺", "note": "多排自然"}
        )
        payload = _extract_payload(msg)
        assert payload == {
            "content": "Day 1 清水寺",
            "has_image": False,
            "previous_summary": "",
            "note": "多排自然",
        }

    def test_import_image_payload_sets_has_image(self):
        payload = _extract_payload(build_import_user_text({"source_type": "image"}))
        assert payload["has_image"] is True


class TestFixedWelcome:
    def test_template_mentions_name_placeholder(self):
        assert "{name}" in FIXED_WELCOME_TEMPLATE
