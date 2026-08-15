"""share-image-v2 三層 prompt 組裝（source-spec 4.4 / 8.5，對照 ShareImageV2Service）。

第 1 層 art_style（image_art_style.txt，原 DCS anime 版）
第 2 層 素材主體（hero → image_hero_scene.txt 純場景版；stamp/tag → 原 DCS prompt）
第 3 層 硬約束（本檔常數，**寫死在程式碼**、prompt 檔改不掉，且放最後含否決權——技法 6/7）

與 PHP 的適配差異：
- hero 一律用純場景版（Cloudflare 不支援參考圖 → 旅伴合成砍掉，migration-plan 3.3 降級 ③）；
  image_hero.txt（DCS hero_prompt）是「結合旅伴照片」專用版，此處不讀
- placeholder 用 PHP 同款單括號 {name}（str.replace 替換，不走 Jinja2——內容含 JSON 無虞但沿用原始語法）
"""

from app.core.prompt_loader import render_prompt

# 無論 prompt 檔給什麼都強制附加：場景圖絕不渲染文字
HERO_HARD_CONSTRAINTS = (
    "Absolutely NO text, NO letters, NO numbers, NO readable signage, NO storefront sign, "
    "NO logo, NO watermark, NO poster layout, NO frame or card border."
)
# 郵戳：深炭黑底 + 淺色高對比線條（App 端疊深色半透明卡片）；覆蓋 art_style 的暖色調（僅限背景）
STAMP_HARD_CONSTRAINTS = (
    "Single centered stamp, no shadow. The background MUST be a solid deep charcoal near-black "
    "color (approximately #1A1A1A) — NOT white, NOT cream, NOT ivory; override any white, cream, "
    "or warm/ivory palette from the art style or body prompt above for the background specifically. "
    "The stamp illustration itself must use light, high-contrast ink tones (NOT dark ink) so it "
    "remains clearly legible against this dark background. Decorative unreadable marks are allowed. "
    "NO real brand, NO real logo, NO watermark."
)
# tag 插畫：純白底供 App 去背；主體置中 70%；覆蓋 art_style 暖色調（僅限背景）
TAG_HARD_CONSTRAINTS = (
    "One centered visual idea, subject fully contained in the center 70%, no shadow. The background "
    "MUST be pure clean white (#FFFFFF) with absolutely zero cream, ivory, beige, or warm tint — "
    "override any warm/ivory palette from the art style instructions above for the background "
    "specifically; only the illustrated subject itself may use the muted color palette. NO text, "
    "NO letters, NO numbers, NO hashtag, NO logo, NO watermark, NO frame or border."
)

MAX_TAGS = 3


def _art_style() -> str:
    return render_prompt("phase1/image_art_style").strip()


def _compose(body: str, constraints: str) -> str:
    art_style = _art_style()
    prefix = f"{art_style}\n\n" if art_style else ""
    return f"{prefix}{body}\n\n{constraints}"


def _destination(analysis: dict) -> str:
    return str(analysis.get("destination_en") or "") or str(analysis.get("destination_cn") or "")


def tag_values(analysis: dict) -> list[str]:
    return [str(t) for t in (analysis.get("highlight_tags") or [])[:MAX_TAGS]]


def build_hero_prompt(analysis: dict) -> str:
    body = render_prompt("phase1/image_hero_scene").strip()
    scene = body.replace(
        "{travel_identity}", str(analysis.get("travel_identity") or "")
    ).replace(
        "{destination_en}", str(analysis.get("destination_en") or "")
    ).replace(
        "{destination_cn}", str(analysis.get("destination_cn") or "")
    )
    return _compose(scene, HERO_HARD_CONSTRAINTS)


def build_stamp_prompt(analysis: dict) -> str:
    body = render_prompt("phase1/image_stamp").strip()
    return _compose(body.replace("{destination}", _destination(analysis)), STAMP_HARD_CONSTRAINTS)


def build_tag_prompt(analysis: dict, index: int) -> str:
    tags = tag_values(analysis)
    if index < 0 or index >= len(tags):
        raise IndexError(f"highlight_tags index {index} does not exist")
    body = render_prompt("phase1/image_tag").strip()
    body = body.replace("{destination}", _destination(analysis)).replace("{tag}", tags[index])
    return _compose(body, TAG_HARD_CONSTRAINTS)
