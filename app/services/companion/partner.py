"""ai_partner / quiz dimensions 本地資料載入與 persona 文字解析。

原始服務讀 DCS（DcsHelper::getAiPartner / getAiQuiz），本服務一律讀 repo 內
data/*.json（技術決策：設定檔進版控，好 diff、好調校）。

resolve_partner_text 對照 PHP CompanionService::resolvePartnerText()：
tag（機器可讀 id）→ label＋description（人類可讀文字，組進 prompt）；
查無 tag 時 label fallback 為 tag 本身（向下相容）。
"""

import json
from functools import lru_cache

from app.config import settings


@lru_cache(maxsize=1)
def load_ai_partner() -> dict:
    """回傳 ai_partner variant（人格/說話風格/性別/頭像等選項）。"""
    raw = json.loads((settings.data_dir / "ai_partner.json").read_text(encoding="utf-8"))
    return raw.get("variant") or {}


@lru_cache(maxsize=1)
def load_quiz_dimensions() -> list[dict]:
    """回傳測驗題庫 dimensions（原 DCS ai_quiz.dimensions）。"""
    raw = json.loads((settings.data_dir / "quiz_dimensions.json").read_text(encoding="utf-8"))
    return raw.get("dimensions") or []


@lru_cache(maxsize=1)
def load_quiz_image_config() -> dict:
    """產圖尺寸/品質/版本設定（原 DCS ai_quiz variant 的非 prompt 欄位）。"""
    raw = json.loads((settings.data_dir / "quiz_dimensions.json").read_text(encoding="utf-8"))
    return raw.get("image_config") or {}


def partner_tags(field: str) -> list[str]:
    """某欄位（personality/speech_style/gender）的合法 tag 清單，供 request 動態驗證。

    對照 PHP SelfIntroductionRequest::tagsFrom()：相容 {tag,label} 物件或純字串兩種格式。
    """
    tags = []
    for item in load_ai_partner().get(field) or []:
        if isinstance(item, dict) and item.get("tag"):
            tags.append(str(item["tag"]))
        elif isinstance(item, str):
            tags.append(item)
    return tags


def _labeled_text(items: list, tag: str) -> str:
    info = next((i for i in items if isinstance(i, dict) and i.get("tag") == tag), {})
    label = str(info.get("label") or tag)
    description = str(info.get("description") or "")
    return f"{label}：{description}" if description else label


def resolve_partner_text(personality, speech_style: str) -> dict:
    """tag → 「label：description」文字（personality 相容單值或清單）。"""
    partner = load_ai_partner()
    personality_tags = personality if isinstance(personality, list) else [personality]
    personality_text = "、".join(
        _labeled_text(partner.get("personality") or [], str(tag)) for tag in personality_tags
    )
    return {
        "personality_text": personality_text,
        "speech_style_text": _labeled_text(partner.get("speech_style") or [], str(speech_style)),
    }


def resolve_gender_text(gender: str) -> str:
    for item in load_ai_partner().get("gender") or []:
        if isinstance(item, dict) and item.get("tag") == gender:
            return str(item.get("label") or gender)
        if isinstance(item, str) and item == gender:
            return gender
    return gender


def resolve_tag_labels(tag_ids: list) -> list[str]:
    """答題 tag ID（如 t1-1）→ 繁中 label；查無 ID 原樣保留（向下相容）。"""
    tag_map = {
        str(tag.get("id", "")): str(tag.get("label", ""))
        for dimension in load_quiz_dimensions()
        for tag in dimension.get("tags") or []
    }
    return [tag_map.get(str(tag_id)) or str(tag_id) for tag_id in tag_ids]
