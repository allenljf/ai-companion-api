"""PostgresKV / PostgresGallery 整合測試（打真實 Neon，需 DATABASE_URL）。

執行：pytest tests/integration/test_storage_postgres.py（.env 有 DATABASE_URL 即會跑，CI 無則跳過）
驗收（migration-plan 階段 6）：TTL 過期讀不到、併發鎖只有一個拿到、gallery 100 筆裁切。
"""

import asyncio
import os
import uuid

import pytest
import pytest_asyncio
from dotenv import dotenv_values

DATABASE_URL = os.environ.get("DATABASE_URL") or dotenv_values(".env").get("DATABASE_URL")

pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL not set")


@pytest_asyncio.fixture
async def kv():
    from app.storage.pg import PostgresKV

    kv = PostgresKV(DATABASE_URL)
    yield kv
    await kv.close()


@pytest_asyncio.fixture
async def gallery():
    from app.storage.pg import PostgresGallery

    gallery = PostgresGallery(DATABASE_URL, table=f"gallery_test_{uuid.uuid4().hex[:8]}")
    yield gallery
    await gallery.drop_table()
    await gallery.close()


def key() -> str:
    return f"test:{uuid.uuid4()}"


@pytest.mark.asyncio
async def test_set_get_roundtrip(kv):
    k = key()
    await kv.set(k, {"a": 1, "中文": "值"}, ttl_seconds=60)
    assert await kv.get(k) == {"a": 1, "中文": "值"}
    await kv.delete(k)


@pytest.mark.asyncio
async def test_missing_key_none(kv):
    assert await kv.get(key()) is None


@pytest.mark.asyncio
async def test_expired_key_none(kv):
    k = key()
    await kv.set(k, "v", ttl_seconds=-1)  # 已過期
    assert await kv.get(k) is None


@pytest.mark.asyncio
async def test_set_overwrites(kv):
    k = key()
    await kv.set(k, "old", ttl_seconds=60)
    await kv.set(k, "new", ttl_seconds=60)
    assert await kv.get(k) == "new"
    await kv.delete(k)


@pytest.mark.asyncio
async def test_add_lock_semantics(kv):
    k = key()
    assert await kv.add(k, True, ttl_seconds=60) is True
    assert await kv.add(k, True, ttl_seconds=60) is False
    await kv.delete(k)
    assert await kv.add(k, True, ttl_seconds=60) is True
    await kv.delete(k)


@pytest.mark.asyncio
async def test_add_reacquires_expired_lock(kv):
    k = key()
    await kv.add(k, True, ttl_seconds=-1)
    assert await kv.add(k, True, ttl_seconds=60) is True
    await kv.delete(k)


@pytest.mark.asyncio
async def test_concurrent_add_only_one_wins(kv):
    # 驗收：兩個（十個）並行請求只有一個拿到鎖——原子性靠單一 SQL 語句
    k = key()
    results = await asyncio.gather(*[kv.add(k, i, ttl_seconds=60) for i in range(10)])
    assert results.count(True) == 1
    await kv.delete(k)


@pytest.mark.asyncio
async def test_gallery_push_and_list_newest_first(gallery):
    for i in range(3):
        await gallery.push({"n": i})
    records = await gallery.list()
    assert [r["n"] for r in records] == [2, 1, 0]


@pytest.mark.asyncio
async def test_gallery_trims_to_limit(gallery):
    # 驗收：超過 100 筆會裁切（測試用小上限）
    gallery.limit = 5
    for i in range(8):
        await gallery.push({"n": i})
    records = await gallery.list()
    assert len(records) == 5
    assert [r["n"] for r in records] == [7, 6, 5, 4, 3]
