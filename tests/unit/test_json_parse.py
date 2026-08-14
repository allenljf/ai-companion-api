"""decode_llm_json()：容忍 LLM 各種不乖的輸出格式（migration-plan 階段 1）。

對應 PHP CompanionService/TravelSummaryService 的 decodeLlmJson()：
strip ```json 框後 json_decode，解不出來回 None（軟失敗上游處理）。
"""

from app.core.json_parse import decode_llm_json


class TestDecodeLlmJson:
    def test_pure_json(self):
        assert decode_llm_json('{"summary": "哈囉", "city": "大阪"}') == {
            "summary": "哈囉",
            "city": "大阪",
        }

    def test_json_fenced_with_language_tag(self):
        raw = '```json\n{"summary": "哈囉"}\n```'
        assert decode_llm_json(raw) == {"summary": "哈囉"}

    def test_json_fenced_without_language_tag(self):
        raw = '```\n{"summary": "哈囉"}\n```'
        assert decode_llm_json(raw) == {"summary": "哈囉"}

    def test_fenced_with_surrounding_prose(self):
        # 模型愛在框外加解說；框內才是 JSON
        raw = '好的，以下是結果：\n```json\n{"summary": "哈囉"}\n```\n希望有幫助！'
        assert decode_llm_json(raw) == {"summary": "哈囉"}

    def test_leading_and_trailing_whitespace(self):
        assert decode_llm_json('  \n {"summary": "哈囉"} \n ') == {"summary": "哈囉"}

    def test_not_json_returns_none(self):
        assert decode_llm_json("我不會輸出 JSON，抱歉") is None

    def test_scalar_json_returns_none(self):
        # 合法 JSON 但不是物件/陣列（PHP is_array 檢查的對應）
        assert decode_llm_json('"just a string"') is None
        assert decode_llm_json("42") is None

    def test_empty_string_returns_none(self):
        assert decode_llm_json("") is None

    def test_truncated_json_returns_none(self):
        assert decode_llm_json('{"summary": "被 max_tokens 切') is None
