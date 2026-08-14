"""LLM 文案長度保險截斷（對照 reference/traits/LlmTextTruncateTrait.php）。"""

import re

# 上限內最後一個句末標點（。！？!?…，含收尾引號）；greedy .* 讓匹配落在「最後」一個句尾
_SENTENCE_END_RE = re.compile(r"^.*[。！？!?…][」』\"']?", re.DOTALL)


def truncate_at_sentence(text: str, max_length: int) -> str:
    """未超過上限原樣回傳；超過時退到上限內最後一個完整句尾，找不到句尾才硬切保底。"""
    if len(text) <= max_length:
        return text

    cut = text[:max_length]
    match = _SENTENCE_END_RE.match(cut)
    return match.group(0) if match else cut
