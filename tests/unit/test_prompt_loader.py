"""prompt 載入機制（Jinja2）：prompt 含 JSON 範例時不會炸（階段 1 驗收）。"""

import pytest

from app.core.prompt_loader import render_prompt


class TestRenderPrompt:
    def test_renders_persona_variable(self):
        text = render_prompt("phase2/summary_quiz", persona="你的名字是「小旅」。")
        assert "你的名字是「小旅」。" in text

    def test_json_example_braces_survive(self):
        # prompt 檔內含 {"summary": "string"} 範例——這是選 Jinja2 而非 str.format 的原因
        text = render_prompt("phase2/summary_quiz", persona="x")
        assert '{"summary": "string"}' in text

    def test_no_unrendered_jinja_left(self):
        for name in ("phase2/summary_quiz", "phase2/summary_order_pick", "phase2/summary_import"):
            text = render_prompt(name, persona="x")
            assert "{{" not in text and "}}" not in text

    def test_import_prompt_outputs_summary_and_city(self):
        # B 入口才讓 LLM 輸出 city（source-spec 5.1）
        text = render_prompt("phase2/summary_import", persona="x")
        assert '{"summary": "string", "city": "string"}' in text

    def test_authority_prompts_output_summary_only(self):
        # A/A2 的輸出 JSON 只有 summary，連 city 欄位都不讓 LLM 輸出
        for name in ("phase2/summary_quiz", "phase2/summary_order_pick"):
            text = render_prompt(name, persona="x")
            assert '{"summary": "string"}' in text
            assert '"city"' not in text

    def test_missing_variable_raises(self):
        # StrictUndefined：漏傳變數要炸在開發期，不要默默輸出空字串
        with pytest.raises(Exception):
            render_prompt("phase2/summary_quiz")
