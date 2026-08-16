"""share-image-v2：App 端合成海報的素材產圖（source-spec 4.4，對照 ShareImageV2Service.php）。

素材槽位：hero（per-completion 快取）、stamp（以 destination 為 key **跨用戶共用快取**）。
`ready` = 本次啟動的 job 全部 settled（遠端失敗仍 ready、URL 為 null，App 以 fallback_category
降級）；`processing` 僅在 completion lock 被他人持有且 hero 尚未存在時回。
status 沒有 failed、share_fallback/fail_reason 恆 null。

**tag icon 素材暫停用**（見下方 GENERATE_TAG_ICONS）：2026-08-16 線上實測發現 Vertex
`gemini-3.1-flash-image` 的產圖配額是 GenContentImageGenRequestsPerMinutePerProjectPerBaseModelGlobal
= **每分鐘 2 張、僅 global 端點**（區域端點 404、Imagen 系列無獨立配額可繞），一次請求要產
5 張（1 hero + 1 stamp + 3 tag）必超額；tag 排最後產生所以最常失敗。決定：先只產 hero + stamp，
tag icon 相關程式碼保留、以 GENERATE_TAG_ICONS 開關控制，額度調升或改用其他 provider 時改回 True
即可重新啟用（decoration_slots/_response 已按此開關寫好條件分支，不需要再改邏輯）。

冪等：hero key 含（uuid | 模型 | 尺寸 | PROMPT_VERSION | hero prompt 全文 SHA-256 前 12 碼）——
調整 prompt 檔後同一 uuid 重打會自動換 key 重新產圖，調校時不用重跑測驗。

對 Cloudflare 的適配（與 PHP 差異）：
- 無參考圖/旅伴合成（CF 忽略 image 欄位，spike 實證）→ hero 純文字 prompt；
  partner_image_url 收下但忽略（App 相容），key 因子不含參考圖版本
- 產出為 JPEG（非 PNG）→ 驗證/上傳依實際 magic bytes
"""

import asyncio
import hashlib
import logging
import re
import time
from datetime import datetime, timezone

from app.core.truncate import truncate_at_sentence
from app.services.companion.share_fallback import hero_fallback_category, tag_fallback_category
from app.services.companion.share_prompts import (
    MAX_TAGS,
    build_hero_prompt,
    build_stamp_prompt,
    build_tag_prompt,
    tag_values,
)
from app.services.image.client import ImageProvider, image_content_type

logger = logging.getLogger(__name__)

PROMPT_VERSION = "v2-hero-app-compose-cf1"
LOCK_PREFIX = "companion:share-v2-lock:"
# hero 產圖預期 <30s；短 TTL 讓 process 被砍時使用者不會卡 processing 太久
LOCK_TTL = 120
PACK_LOCK_PREFIX = "companion:pack-lock:"
PACK_LOCK_TTL = 120
CACHE_KEY_PREFIX = "companion:completion:"
QUOTE_MAX_LENGTH = 30
S3_PREFIX = "share-v2"

from app.config import settings

# 模型由 settings 決定（預設 Vertex gemini-3.1-flash-image；IMAGE_*_MODEL env 可覆寫）。
# 模型 id 是冪等 key 的因子之一——換模型自動換 key 重新產圖，不會誤用舊快取
HERO_MODEL = settings.image_hero_model
HERO_WIDTH, HERO_HEIGHT = 1152, 2048  # Gemini provider 轉為 9:16 + 2K（實際 1536×2752）
DECORATION_MODEL = settings.image_decoration_model
DECORATION_STEPS = 8  # 只對 CF flux-schnell 有意義；Gemini provider 忽略

# tag icon 產圖開關：見上方模組說明。False 時 decoration_slots() 只回 stamp、
# _response() 的 tag_icon_urls/tag_fallback_categories 回空陣列（不產、不計費、不回 URL）。
GENERATE_TAG_ICONS = False


class QuizSessionNotFound(Exception):
    """分析快取不存在（過期或未完成測驗）→ 路由層回 HTTP 200 + metadata.status=C007。"""


class ShareImageV2Service:
    def __init__(
        self,
        *,
        provider: ImageProvider,
        kv,
        gallery,
        store,
        poll_interval: float = 0.2,
        pack_wait_timeout: float = PACK_LOCK_TTL,
        max_concurrency: int = 2,
        retry_delays: tuple = (20.0,),
    ):
        self._provider = provider
        self._kv = kv
        self._gallery = gallery
        self._store = store
        self._poll_interval = poll_interval
        self._pack_wait_timeout = pack_wait_timeout
        # Vertex 試用帳戶產圖 RPM 低（線上實測 5 張並行必撞 429）：
        # 併發壓到 2 + 429 退避重試；LOCK_TTL 120s 內完成綽綽有餘
        self._max_concurrency = max_concurrency
        self._retry_delays = retry_delays

    async def generate(self, completion_uuid: str, partner_image_url: str | None = None) -> dict:
        cached = await self._kv.get(CACHE_KEY_PREFIX + completion_uuid)
        if not cached or not cached.get("analysis"):
            raise QuizSessionNotFound(completion_uuid)

        analysis = cached["analysis"]
        companion_name = str(cached.get("companion_name") or "")
        hero_key = self.hero_key(completion_uuid, analysis)
        hero_exists = await self._store.exists(hero_key)

        lock_key = LOCK_PREFIX + completion_uuid
        lock_acquired = False
        if not hero_exists:
            if not await self._kv.add(lock_key, True, ttl_seconds=LOCK_TTL):
                # completion lock 已被他人持有：立即回 processing，不啟動任何 job
                return await self._response("processing", completion_uuid, analysis, companion_name)
            lock_acquired = True

        try:
            await self._generate_assets(analysis, hero_key, hero_exists)

            if lock_acquired and await self._store.exists(hero_key):
                try:
                    await self._append_gallery(
                        analysis, companion_name,
                        self._store.url(hero_key), cached.get("partner_avatar_url"),
                    )
                except Exception:
                    logger.warning("append gallery failed", extra={"uuid": completion_uuid})
        finally:
            if lock_acquired:
                await self._kv.delete(lock_key)

        return await self._response("ready", completion_uuid, analysis, companion_name)

    # ------------------------------------------------------------------ jobs

    async def _generate_assets(self, analysis: dict, hero_key: str, hero_exists: bool) -> None:
        jobs: dict[str, tuple] = {}  # slot -> (key, coroutine factory args)
        locked_packs: list[str] = []
        contended: list[tuple[str, str]] = []  # (lock_key, object_key)
        scheduled_keys: set[str] = set()

        if not hero_exists:
            jobs["hero"] = (hero_key, build_hero_prompt(analysis), HERO_MODEL, HERO_WIDTH, HERO_HEIGHT)

        for slot, key in self.decoration_slots(analysis).items():
            if await self._store.exists(key):
                continue
            # 重複 tag → 相同 key：本請求已排定就對齊既有槽位，不重複計費
            if key in scheduled_keys:
                continue
            pack_lock = PACK_LOCK_PREFIX + key
            if await self._kv.add(pack_lock, True, ttl_seconds=PACK_LOCK_TTL):
                locked_packs.append(pack_lock)
                prompt = (
                    build_stamp_prompt(analysis) if slot == "stamp"
                    else build_tag_prompt(analysis, int(slot.removeprefix("tag_")))
                )
                jobs[slot] = (key, prompt, DECORATION_MODEL, None, None)
            else:
                contended.append((pack_lock, key))
            scheduled_keys.add(key)

        try:
            if jobs:
                semaphore = asyncio.Semaphore(self._max_concurrency)
                await asyncio.gather(
                    *(self._run_job(semaphore, slot, *spec) for slot, spec in jobs.items())
                )
            await self._await_contended(contended)
        finally:
            for pack_lock in locked_packs:
                await self._kv.delete(pack_lock)

    async def _run_job(self, semaphore: asyncio.Semaphore, slot: str, key: str, prompt: str,
                       model: str, width: int | None, height: int | None) -> None:
        """單一槽位產圖 + 上傳；429 退避重試，其他失敗只記錄（partial-fail 不影響其他槽位）。"""
        async with semaphore:
            started = time.monotonic()
            last_error: Exception | None = None
            for attempt, delay in enumerate((0.0, *self._retry_delays)):
                if delay:
                    await asyncio.sleep(delay)
                try:
                    data = await self._provider.generate(
                        prompt, model=model, width=width, height=height,
                        steps=None if model == HERO_MODEL else DECORATION_STEPS,
                    )
                    await self._store.upload(key, data, content_type=image_content_type(data))
                    # 錯誤/耗時直接進訊息字串（Cloud Run 的 textPayload 看不到 logging extra）
                    logger.info(
                        "asset generated: %s in %dms (attempt %d)",
                        slot, int((time.monotonic() - started) * 1000), attempt + 1,
                    )
                    return
                except Exception as exc:
                    last_error = exc
                    if "429" not in str(exc):
                        break  # 只有配額類錯誤值得重試
            logger.warning("asset generation failed: %s: %s", slot, last_error)

    async def _await_contended(self, contended: list[tuple[str, str]]) -> None:
        """他人持有 pack lock 的槽位輪詢等待：鎖清除或物件出現即停；絕不刪除他人鎖。"""
        for lock_key, object_key in contended:
            deadline = time.monotonic() + self._pack_wait_timeout
            while time.monotonic() < deadline:
                if await self._kv.get(lock_key) is None:
                    break
                if await self._store.exists(object_key):
                    break
                await asyncio.sleep(self._poll_interval)

    # ------------------------------------------------------------------ keys

    def hero_key(self, completion_uuid: str, analysis: dict) -> str:
        prompt_version = hashlib.sha256(build_hero_prompt(analysis).encode()).hexdigest()[:12]
        digest = hashlib.md5(
            "|".join([
                completion_uuid, HERO_MODEL, f"{HERO_WIDTH}x{HERO_HEIGHT}",
                PROMPT_VERSION, prompt_version,
            ]).encode()
        ).hexdigest()
        return f"{S3_PREFIX}/{digest}-hero.png"

    def decoration_slots(self, analysis: dict) -> dict[str, str]:
        """stamp 以 destination、tag 以 destination+tag 為 key（跨用戶共用快取）。

        tag icon 暫停用（GENERATE_TAG_ICONS=False）：只回傳 stamp 槽位，
        _generate_assets() 的迴圈自然就不會排 tag job。
        """
        version = hashlib.md5(
            f"{DECORATION_MODEL}|square|{PROMPT_VERSION}".encode()
        ).hexdigest()[:8]
        dest_slug = self._destination_slug(analysis)

        slots = {"stamp": f"{S3_PREFIX}/packs/dest/{dest_slug}/stamp-{version}.png"}
        if GENERATE_TAG_ICONS:
            for index, tag in enumerate(tag_values(analysis)):
                tag_hash = hashlib.sha256(
                    f"{dest_slug}|{tag.strip().lower()}".encode()
                ).hexdigest()[:16]
                slots[f"tag_{index}"] = f"{S3_PREFIX}/packs/tag/{tag_hash}-{version}.png"
        return slots

    def _destination_slug(self, analysis: dict) -> str:
        """檔名安全 slug（不外洩原始目的地字串）。"""
        slug = re.sub(r"[^a-z0-9]+", "-", str(analysis.get("destination_en") or "").lower()).strip("-")
        if not slug:
            slug = hashlib.md5(str(analysis.get("destination_cn") or "unknown").encode()).hexdigest()[:12]
        return slug

    # -------------------------------------------------------------- response

    async def _response(self, status: str, completion_uuid: str,
                        analysis: dict, companion_name: str) -> dict:
        hero_key = self.hero_key(completion_uuid, analysis)
        decorations = self.decoration_slots(analysis)
        tags = tag_values(analysis)

        stamp_key = decorations.get("stamp")
        # tag icon 暫停用：不產也不回 URL（見 GENERATE_TAG_ICONS）；highlight_tags 文字仍在 content 裡完整回傳
        tag_urls: list[str | None] = []
        tag_categories: list[str] = []
        if GENERATE_TAG_ICONS:
            for index in range(MAX_TAGS):
                key = decorations.get(f"tag_{index}")
                tag_urls.append(
                    self._store.url(key) if key and await self._store.exists(key) else None
                )
                tag_categories.append(
                    tag_fallback_category(tags[index]) if index < len(tags) else "generic"
                )

        return {
            "status": status,
            "hero_url": self._store.url(hero_key) if await self._store.exists(hero_key) else None,
            "hero_fallback_category": hero_fallback_category(analysis),
            "content": {
                "travel_identity": str(analysis.get("travel_identity") or ""),
                "travel_identity_en": str(analysis.get("travel_identity_en") or ""),
                "destination_cn": str(analysis.get("destination_cn") or ""),
                "destination_en": str(analysis.get("destination_en") or ""),
                "tagline": str(analysis.get("tagline") or ""),
                "highlight_tags": tags,
                "companion_quote": truncate_at_sentence(
                    str(analysis.get("companion_quote") or ""), QUOTE_MAX_LENGTH
                ),
                "companion_name": companion_name or "Companion",
            },
            "decorations": {
                "stamp_url": (
                    self._store.url(stamp_key)
                    if stamp_key and await self._store.exists(stamp_key)
                    else None
                ),
                "stamp_fallback_category": "generic",
                "tag_icon_urls": tag_urls,
                "tag_fallback_categories": tag_categories,
            },
            "share_fallback": None,
            "fail_reason": None,
        }

    async def _append_gallery(self, analysis: dict, companion_name: str,
                              hero_url: str, partner_avatar_url) -> None:
        await self._gallery.push(
            {
                "travel_identity": str(analysis.get("travel_identity") or ""),
                "travel_identity_en": str(analysis.get("travel_identity_en") or ""),
                "destination_cn": str(analysis.get("destination_cn") or ""),
                "destination_en": str(analysis.get("destination_en") or ""),
                "destination_country": str(analysis.get("destination_country") or ""),
                "tagline": str(analysis.get("tagline") or ""),
                "highlight_tags": tag_values(analysis),
                "companion_quote": truncate_at_sentence(
                    str(analysis.get("companion_quote") or ""), QUOTE_MAX_LENGTH
                ),
                "companion_name": companion_name or "Companion",
                "partner_avatar_url": partner_avatar_url,
                "share_image_url": hero_url,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
        )
