"""簡體 → 繁體（台灣標準）後處理。

風險 #3 的 fallback：prompt 強調繁體後仍有單字級滲漏（如「预算」），
在輸出端做確定性轉換；已是繁體的文字轉換後不變。
"""

from opencc import OpenCC

_converter = OpenCC("s2twp")


def to_traditional(text: str) -> str:
    return _converter.convert(text) if text else text
