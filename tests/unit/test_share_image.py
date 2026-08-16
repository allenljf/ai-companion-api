"""share-image-v2 service 測試（source-spec 4.4，fake provider/store/kv/gallery）。

驗收（migration-plan 8a）：冪等（同 uuid 重打不重產）、併發鎖（搶不到回 processing）、
逐素材獨立成敗、status 只有 ready/processing、hero 成功才寫 gallery。
"""

import asyncio

import pytest

from app.services.companion.completion import CACHE_KEY_PREFIX
from app.services.companion.share_image import (
    LOCK_PREFIX,
    QuizSessionNotFound,
    ShareImageV2Service,
)
from app.services.image.client import ImageGenerationError
from app.storage.gallery import InMemoryGallery
from app.storage.kv import InMemoryKV
from app.storage.objects import InMemoryObjectStore

UUID = "b0e7a3a8-8f2f-4c1e-9d2f-1c9a35c1dbbb"
JPEG = b"\xff\xd8\xff\xe0" + b"img"

ANALYSIS = {
    "travel_identity": "獨處療癒師",
    "travel_identity_en": "Solo Healer",
    "destination_cn": "京都",
    "destination_en": "Kyoto",
    "destination_country": "日本",
    "tagline": "慢慢走就好",
    "highlight_tags": ["身心療癒派", "大自然充電族", "安心舒適圈"],
    "companion_quote": "慢慢走，才看得到風景。",
}


class FakeProvider:
    def __init__(self):
        self.calls: list[dict] = []
        self.fail_prompts: list[str] = []  # prompt 含這些子字串就拋錯

    async def generate(self, prompt, *, model, width=None, height=None, steps=None):
        self.calls.append({"prompt": prompt, "model": model, "width": width, "height": height})
        for needle in self.fail_prompts:
            if needle in prompt:
                raise ImageGenerationError(f"boom: {needle}")
        return JPEG


async def make_service(**overrides):
    kv = overrides.get("kv") or InMemoryKV()
    await kv.set(
        CACHE_KEY_PREFIX + UUID,
        {"analysis": dict(ANALYSIS), "companion_name": "阿旅", "partner_avatar_url": None},
        ttl_seconds=3600,
    )
    service = ShareImageV2Service(
        provider=overrides.get("provider") or FakeProvider(),
        kv=kv,
        gallery=overrides.get("gallery") or InMemoryGallery(),
        store=overrides.get("store") or InMemoryObjectStore(),
        poll_interval=0.001,
        pack_wait_timeout=0.01,
        retry_delays=overrides.get("retry_delays", (0.0,)),
    )
    return service


@pytest.mark.asyncio
async def test_missing_analysis_cache_raises_c007():
    service = ShareImageV2Service(
        provider=FakeProvider(), kv=InMemoryKV(), gallery=InMemoryGallery(),
        store=InMemoryObjectStore(),
    )
    with pytest.raises(QuizSessionNotFound):
        await service.generate(UUID)


@pytest.mark.asyncio
async def test_first_call_generates_hero_and_stamp_only_and_returns_ready():
    # tag icon 暫停用（Vertex 產圖配額每分鐘 2 張，一次 5 張必超額，見 GENERATE_TAG_ICONS）
    provider = FakeProvider()
    store = InMemoryObjectStore()
    gallery = InMemoryGallery()
    service = await make_service(provider=provider, store=store, gallery=gallery)

    result = await service.generate(UUID)

    assert result["status"] == "ready"
    assert result["fail_reason"] is None and result["share_fallback"] is None
    assert len(provider.calls) == 2  # hero + stamp（tag icon 停用，不產也不計費）
    assert result["hero_url"] and result["hero_url"].startswith("https://")
    assert result["decorations"]["stamp_url"]
    assert result["decorations"]["tag_icon_urls"] == []
    assert result["decorations"]["tag_fallback_categories"] == []
    assert len(store.objects) == 2

    # hero 走 klein 直式，stamp 走方圖
    hero_call = next(c for c in provider.calls if c["width"] == 1152)
    assert hero_call["height"] == 2048
    assert sum(1 for c in provider.calls if c["width"] is None) == 1

    # content 供 App 排版（highlight_tags 文字仍完整回傳，只是不產對應插畫）
    assert result["content"]["travel_identity"] == "獨處療癒師"
    assert result["content"]["companion_name"] == "阿旅"
    assert result["content"]["highlight_tags"] == ["身心療癒派", "大自然充電族", "安心舒適圈"]
    assert result["hero_fallback_category"] == "culture"


@pytest.mark.asyncio
async def test_idempotent_second_call_generates_nothing():
    provider = FakeProvider()
    store = InMemoryObjectStore()
    service = await make_service(provider=provider, store=store)
    first = await service.generate(UUID)
    calls_after_first = len(provider.calls)

    second = await service.generate(UUID)
    assert len(provider.calls) == calls_after_first  # 全部命中快取，不重產
    assert second["status"] == "ready"
    assert second["hero_url"] == first["hero_url"]


@pytest.mark.asyncio
async def test_completion_lock_held_returns_processing_without_jobs():
    provider = FakeProvider()
    kv = InMemoryKV()
    await kv.add(LOCK_PREFIX + UUID, True, ttl_seconds=60)  # 他人持鎖
    service = await make_service(provider=provider, kv=kv)

    result = await service.generate(UUID)
    assert result["status"] == "processing"
    assert provider.calls == []  # 不啟動任何 job
    assert result["hero_url"] is None
    assert result["content"]["travel_identity"] == "獨處療癒師"  # 文字內容照樣回


@pytest.mark.asyncio
async def test_partial_failure_stamp_only():
    provider = FakeProvider()
    provider.fail_prompts = ["passport-stamp"]
    service = await make_service(provider=provider)

    result = await service.generate(UUID)
    assert result["status"] == "ready"  # 沒有 failed 狀態
    assert result["hero_url"] is not None
    assert result["decorations"]["stamp_url"] is None  # 失敗槽位 URL 為 null
    assert result["fail_reason"] is None


@pytest.mark.asyncio
async def test_hero_failure_skips_gallery():
    provider = FakeProvider()
    provider.fail_prompts = ["travel personality"]  # hero prompt 特徵
    gallery = InMemoryGallery()
    service = await make_service(provider=provider, gallery=gallery)

    result = await service.generate(UUID)
    assert result["status"] == "ready"
    assert result["hero_url"] is None
    assert await gallery.list() == []


@pytest.mark.asyncio
async def test_hero_success_appends_gallery_once():
    gallery = InMemoryGallery()
    service = await make_service(gallery=gallery)
    await service.generate(UUID)
    records = await gallery.list()
    assert len(records) == 1
    assert records[0]["travel_identity"] == "獨處療癒師"
    assert records[0]["share_image_url"].startswith("https://")
    assert records[0]["companion_name"] == "阿旅"

    # 重打命中快取 → 不再寫 gallery
    await service.generate(UUID)
    assert len(await gallery.list()) == 1


@pytest.mark.asyncio
async def test_decorations_shared_across_users_same_destination():
    provider = FakeProvider()
    store = InMemoryObjectStore()
    kv = InMemoryKV()
    service = await make_service(provider=provider, store=store, kv=kv)
    await service.generate(UUID)
    calls_first = len(provider.calls)  # hero + stamp

    other_uuid = "c9999999-8f2f-4c1e-9d2f-1c9a35c1dccc"
    await kv.set(
        CACHE_KEY_PREFIX + other_uuid,
        {"analysis": dict(ANALYSIS), "companion_name": "小K", "partner_avatar_url": None},
        ttl_seconds=3600,
    )
    result = await service.generate(other_uuid)
    # 同目的地：stamp 走跨用戶快取，只重產 hero
    assert len(provider.calls) == calls_first + 1
    assert result["decorations"]["stamp_url"]


@pytest.mark.asyncio
async def test_contended_pack_lock_no_duplicate_job():
    # 他人持有 stamp 的 pack lock 且物件始終沒出現：等待逾時後 stamp URL 為 null、不重複產圖
    provider = FakeProvider()
    kv = InMemoryKV()
    store = InMemoryObjectStore()
    service = await make_service(provider=provider, kv=kv, store=store)

    stamp_key = service.decoration_slots(ANALYSIS)["stamp"]
    await kv.add("companion:pack-lock:" + stamp_key, True, ttl_seconds=60)

    result = await service.generate(UUID)
    assert result["status"] == "ready"
    assert result["decorations"]["stamp_url"] is None
    stamp_prompts = [c for c in provider.calls if "passport-stamp" in c["prompt"]]
    assert stamp_prompts == []  # 沒有重複計費


@pytest.mark.asyncio
async def test_tag_icons_disabled_no_slots_no_jobs_no_urls():
    from app.services.companion.share_image import GENERATE_TAG_ICONS

    assert GENERATE_TAG_ICONS is False  # 明確鎖定目前狀態；改回 True 時這條測試會提醒同步更新其他測試

    provider = FakeProvider()
    service = await make_service(provider=provider)
    result = await service.generate(UUID)

    assert "tag_0" not in service.decoration_slots(ANALYSIS)
    assert not any("destination-specific travel-journal" in c["prompt"] for c in provider.calls)
    assert result["decorations"]["tag_icon_urls"] == []
    assert result["decorations"]["tag_fallback_categories"] == []


@pytest.mark.asyncio
async def test_hero_key_changes_when_prompt_changes():
    service = await make_service()
    key_before = service.hero_key(UUID, ANALYSIS)
    key_after = service.hero_key(UUID, ANALYSIS | {"destination_en": "Osaka"})
    assert key_before != key_after  # 冪等 key 含 hero prompt SHA-256


@pytest.mark.asyncio
async def test_quota_429_retried_and_recovers():
    # Vertex 試用帳戶產圖 RPM 低：429 要退避重試（線上實測 5 張並行會撞）
    class QuotaFlakyProvider(FakeProvider):
        def __init__(self):
            super().__init__()
            self.failed_once: set[str] = set()

        async def generate(self, prompt, *, model, width=None, height=None, steps=None):
            self.calls.append({"prompt": prompt, "model": model, "width": width, "height": height})
            if "passport-stamp" in prompt and "stamp" not in self.failed_once:
                self.failed_once.add("stamp")
                raise ImageGenerationError("Vertex generateContent HTTP 429: RESOURCE_EXHAUSTED")
            return JPEG

    provider = QuotaFlakyProvider()
    service = await make_service(provider=provider)
    result = await service.generate(UUID)
    assert result["decorations"]["stamp_url"] is not None  # 重試後成功


@pytest.mark.asyncio
async def test_non_quota_error_not_retried():
    provider = FakeProvider()
    provider.fail_prompts = ["passport-stamp"]  # 一般錯誤（非 429）
    service = await make_service(provider=provider)
    await service.generate(UUID)
    stamp_calls = [c for c in provider.calls if "passport-stamp" in c["prompt"]]
    assert len(stamp_calls) == 1  # 不重試


@pytest.mark.asyncio
async def test_concurrent_first_calls_generate_once():
    provider = FakeProvider()
    kv = InMemoryKV()
    store = InMemoryObjectStore()
    gallery = InMemoryGallery()
    service = await make_service(provider=provider, kv=kv, store=store, gallery=gallery)

    results = await asyncio.gather(*[service.generate(UUID) for _ in range(3)])
    statuses = sorted(r["status"] for r in results)
    assert statuses.count("processing") == 2 and statuses.count("ready") == 1
    hero_calls = [c for c in provider.calls if c["width"] == 1152]
    assert len(hero_calls) == 1  # hero 只產一次
