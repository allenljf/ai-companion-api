"""LLM 回應的 JSON 解析（decodeLlmJson 等價物，docs/source-spec.md 技法 3）。

哲學：解不出來回 None，由上游做軟失敗；這裡絕不 raise。
"""

import json
import re

_FENCE_RE = re.compile(r"^```(?:json)?\s*$|^```\s*$", re.MULTILINE)
_FENCED_BLOCK_RE = re.compile(r"```(?:json)?\s*\n(.*?)\n```", re.DOTALL)


def decode_llm_json(response: str) -> dict | list | None:
    """把 LLM 的文字回應解析成 JSON 物件；容忍 ```json 框與框外解說文字。"""
    if not isinstance(response, str) or not response.strip():
        return None

    candidates = [response]

    # 有 code fence 時，框內內容優先（模型常在框外加解說）
    fenced = _FENCED_BLOCK_RE.search(response)
    if fenced:
        candidates.insert(0, fenced.group(1))
    else:
        # 只有開頭/結尾的殘缺框（對應 PHP 的逐行 strip）
        candidates.insert(0, _FENCE_RE.sub("", response))

    for text in candidates:
        try:
            parsed = json.loads(text.strip())
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(parsed, (dict, list)):
            return parsed
    return None
