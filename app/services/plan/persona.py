"""旅伴 persona 與 user message 包裝（兩期共用慣例，對照 PHP personaText()/companionName()）。

注意：wish/history 的「判讀邏輯」刻意不共用（source-spec 5.3/5.4），
但 persona 組裝是跨 Phase 的全域慣例，屬於共用基礎設施。
"""

import json

DEFAULT_COMPANION_NAME = "小旅"


def _trim(value) -> str:
    return str(value).strip() if isinstance(value, (str, int, float)) else ""


def companion_name(params: dict) -> str:
    return _trim(params.get("companion_name")) or DEFAULT_COMPANION_NAME


def persona_text(params: dict) -> str:
    name = companion_name(params)
    traits = [t for t in (_trim(params.get("personality")), _trim(params.get("speech_style"))) if t]
    if not traits:
        return f"你的名字是「{name}」。"
    return f"你的名字是「{name}」，人格特質與說話風格關鍵字：{'、'.join(traits)}，請以此語氣說話。"


def wrap_user_input(payload: dict) -> str:
    return "<<<USER_INPUT\n" + json.dumps(payload, ensure_ascii=False) + "\n>>>END_USER_INPUT"
