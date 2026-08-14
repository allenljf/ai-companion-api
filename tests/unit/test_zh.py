"""簡轉繁後處理（風險 #3 fallback：prompt 強調擋不住單字級滲漏，用 OpenCC 保證）。"""

from app.core.zh import to_traditional


class TestToTraditional:
    def test_converts_simplified(self):
        assert to_traditional("预算很够，体验超值") == "預算很夠，體驗超值"

    def test_traditional_unchanged(self):
        text = "沒關係，我們從沖繩開始規劃吧！躺平發呆。"
        assert to_traditional(text) == text

    def test_empty_and_ascii_passthrough(self):
        assert to_traditional("") == ""
        assert to_traditional("hello 123!") == "hello 123!"
