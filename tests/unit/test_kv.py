"""KV 快取測試（async 介面 + add 原子搶鎖；InMemory 實作）。

Postgres 實作走同一組行為測試（tests/integration/test_storage_postgres.py，需 DATABASE_URL）。
"""

import asyncio

import pytest

from app.storage.kv import InMemoryKV


def make_kv(now: list[float]) -> InMemoryKV:
    return InMemoryKV(clock=lambda: now[0])


@pytest.mark.asyncio
async def test_set_get_roundtrip():
    kv = InMemoryKV()
    await kv.set("k", {"a": 1}, ttl_seconds=60)
    assert await kv.get("k") == {"a": 1}


@pytest.mark.asyncio
async def test_missing_key_returns_none():
    assert await InMemoryKV().get("nope") is None


@pytest.mark.asyncio
async def test_expired_key_returns_none():
    now = [1000.0]
    kv = make_kv(now)
    await kv.set("k", "v", ttl_seconds=10)
    now[0] = 1009.9
    assert await kv.get("k") == "v"
    now[0] = 1010.1
    assert await kv.get("k") is None


@pytest.mark.asyncio
async def test_set_overwrites_value_and_ttl():
    now = [0.0]
    kv = make_kv(now)
    await kv.set("k", "old", ttl_seconds=5)
    await kv.set("k", "new", ttl_seconds=100)
    now[0] = 50.0
    assert await kv.get("k") == "new"


@pytest.mark.asyncio
async def test_ttl_none_never_expires():
    # 海報相關資料（分析快取）永久保留，使用者要清自己去 DB 清
    now = [0.0]
    kv = make_kv(now)
    await kv.set("k", "forever", ttl_seconds=None)
    now[0] = 10**9
    assert await kv.get("k") == "forever"


@pytest.mark.asyncio
async def test_delete():
    kv = InMemoryKV()
    await kv.set("k", "v", ttl_seconds=60)
    await kv.delete("k")
    assert await kv.get("k") is None
    await kv.delete("k")  # 不存在也不炸


# ---------------------------------------------------------------------------
# add：原子搶鎖（不存在或已過期才寫入）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_add_acquires_when_absent():
    kv = InMemoryKV()
    assert await kv.add("lock", True, ttl_seconds=60) is True


@pytest.mark.asyncio
async def test_add_fails_when_held():
    kv = InMemoryKV()
    assert await kv.add("lock", True, ttl_seconds=60) is True
    assert await kv.add("lock", True, ttl_seconds=60) is False


@pytest.mark.asyncio
async def test_add_reacquires_after_expiry():
    now = [0.0]
    kv = make_kv(now)
    assert await kv.add("lock", True, ttl_seconds=10) is True
    now[0] = 11.0
    assert await kv.add("lock", True, ttl_seconds=10) is True


@pytest.mark.asyncio
async def test_add_released_by_delete():
    kv = InMemoryKV()
    await kv.add("lock", True, ttl_seconds=60)
    await kv.delete("lock")
    assert await kv.add("lock", True, ttl_seconds=60) is True


@pytest.mark.asyncio
async def test_concurrent_add_only_one_wins():
    kv = InMemoryKV()
    results = await asyncio.gather(*[kv.add("lock", i, ttl_seconds=60) for i in range(10)])
    assert results.count(True) == 1
