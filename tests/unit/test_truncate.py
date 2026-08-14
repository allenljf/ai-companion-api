"""句尾截斷測試（對照 reference/traits/LlmTextTruncateTrait.php）。

行為：未超上限原樣回傳；超限退到上限內「最後一個完整句尾」（。！？!?… 含收尾引號）；
上限內完全沒有句末標點才硬切保底。
"""

from app.core.truncate import truncate_at_sentence


def test_within_limit_returned_as_is():
    assert truncate_at_sentence("嗨嗨！行程排好了。", 60) == "嗨嗨！行程排好了。"


def test_exactly_at_limit_returned_as_is():
    text = "a" * 60
    assert truncate_at_sentence(text, 60) == text


def test_over_limit_cuts_at_last_sentence_end_within_limit():
    # 上限 12 → 先取前 12 字「第一句。第二句！第三句還沒完」→ 退到最後句尾「！」
    text = "第一句。第二句！第三句還沒完就被切掉了"
    assert truncate_at_sentence(text, 12) == "第一句。第二句！"


def test_closing_quote_after_punctuation_is_kept():
    text = "他說「走吧！」然後我們就出發前往下一站了"
    assert truncate_at_sentence(text, 8) == "他說「走吧！」"


def test_no_sentence_punctuation_falls_back_to_hard_cut():
    text = "無標點的長字串" * 10
    assert truncate_at_sentence(text, 10) == text[:10]


def test_ellipsis_counts_as_sentence_end():
    text = "先醞釀一下…然後又講了一大堆完全停不下來的話"
    assert truncate_at_sentence(text, 10) == "先醞釀一下…"


def test_greedy_match_uses_last_punctuation_not_first():
    text = "短句。又一句。尾巴還有很長很長的一段沒說完的話"
    assert truncate_at_sentence(text, 10) == "短句。又一句。"
