"""Neon Postgres 儲存實作（階段 6）：KV（cache/lock）與 gallery LIST。

對應原始服務的 redis_data：跨請求/跨副本共用——quiz-completions 24h 快取、
階段 8 產圖併發鎖與 gallery 都靠這層。

原子性設計：
- add()（搶鎖）是單一 INSERT ... ON CONFLICT DO UPDATE ... WHERE expired 語句，
  由 Postgres 保證原子，不需要交易或 advisory lock
- gallery push 的裁切（LTRIM 等價）與 TTL 清理在每次寫入時順帶執行，
  併發時最壞暫時多一筆，下一次寫入自動修正（同 PHP 的取捨）

TTL 一律存絕對時間（expires_at），過期資料查不到即等同不存在；
set/add 時順手清掉過期列，避免表無限成長（量小，全表清理成本可忽略）。
"""

import json
from typing import Any

from psycopg_pool import AsyncConnectionPool

_KV_SCHEMA = """
CREATE TABLE IF NOT EXISTS kv_cache (
    key TEXT PRIMARY KEY,
    value JSONB NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL
)
"""


class PostgresKV:
    def __init__(self, database_url: str, *, pool_size: int = 3):
        # open=False：lazy 連線，服務啟動不因 DB 暫時不可達而失敗（軟失敗原則）
        self._pool = AsyncConnectionPool(
            database_url, min_size=0, max_size=pool_size, open=False
        )
        self._initialized = False

    async def _conn(self):
        await self._pool.open()
        if not self._initialized:
            async with self._pool.connection() as conn:
                await conn.execute(_KV_SCHEMA)
            self._initialized = True
        return self._pool.connection()

    async def set(self, key: str, value: Any, ttl_seconds: float) -> None:
        async with await self._conn() as conn:
            await conn.execute("DELETE FROM kv_cache WHERE expires_at <= now()")
            await conn.execute(
                """
                INSERT INTO kv_cache (key, value, expires_at)
                VALUES (%s, %s::jsonb, now() + make_interval(secs => %s))
                ON CONFLICT (key) DO UPDATE
                    SET value = EXCLUDED.value, expires_at = EXCLUDED.expires_at
                """,
                (key, json.dumps(value, ensure_ascii=False), ttl_seconds),
            )

    async def get(self, key: str) -> Any | None:
        async with await self._conn() as conn:
            cursor = await conn.execute(
                "SELECT value FROM kv_cache WHERE key = %s AND expires_at > now()", (key,)
            )
            row = await cursor.fetchone()
        return row[0] if row else None

    async def delete(self, key: str) -> None:
        async with await self._conn() as conn:
            await conn.execute("DELETE FROM kv_cache WHERE key = %s", (key,))

    async def add(self, key: str, value: Any, ttl_seconds: float) -> bool:
        """不存在或已過期才寫入（搶鎖語意）；單一 SQL 語句，Postgres 保證原子。"""
        async with await self._conn() as conn:
            cursor = await conn.execute(
                """
                INSERT INTO kv_cache (key, value, expires_at)
                VALUES (%s, %s::jsonb, now() + make_interval(secs => %s))
                ON CONFLICT (key) DO UPDATE
                    SET value = EXCLUDED.value, expires_at = EXCLUDED.expires_at
                    WHERE kv_cache.expires_at <= now()
                RETURNING key
                """,
                (key, json.dumps(value, ensure_ascii=False), ttl_seconds),
            )
            row = await cursor.fetchone()
        return row is not None

    async def close(self) -> None:
        await self._pool.close()


class PostgresGallery:
    """測驗結果牆（對照 Redis LIST + LTRIM + EXPIRE）：只保留最新 limit 筆、TTL 一週。"""

    def __init__(
        self,
        database_url: str,
        *,
        table: str = "gallery_items",
        limit: int = 100,
        ttl_seconds: int = 604800,
        pool_size: int = 3,
    ):
        assert table.replace("_", "").isalnum(), "table name must be a safe identifier"
        self._table = table
        self.limit = limit
        self.ttl_seconds = ttl_seconds
        self._pool = AsyncConnectionPool(
            database_url, min_size=0, max_size=pool_size, open=False
        )
        self._initialized = False

    async def _conn(self):
        await self._pool.open()
        if not self._initialized:
            async with self._pool.connection() as conn:
                await conn.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {self._table} (
                        id BIGSERIAL PRIMARY KEY,
                        record JSONB NOT NULL,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                    )
                    """
                )
            self._initialized = True
        return self._pool.connection()

    async def push(self, record: dict) -> None:
        """寫入一筆並裁切到最新 limit 筆 + 清過期（各語句原子，併發最壞暫多一筆）。"""
        async with await self._conn() as conn:
            await conn.execute(
                f"INSERT INTO {self._table} (record) VALUES (%s::jsonb)",
                (json.dumps(record, ensure_ascii=False),),
            )
            await conn.execute(
                f"""
                DELETE FROM {self._table}
                WHERE id NOT IN (SELECT id FROM {self._table} ORDER BY id DESC LIMIT %s)
                   OR created_at <= now() - make_interval(secs => %s)
                """,
                (self.limit, self.ttl_seconds),
            )

    async def list(self) -> list[dict]:
        """新到舊；過期不回（TTL 安全網，裁切才是主要保留機制）。"""
        async with await self._conn() as conn:
            cursor = await conn.execute(
                f"""
                SELECT record FROM {self._table}
                WHERE created_at > now() - make_interval(secs => %s)
                ORDER BY id DESC LIMIT %s
                """,
                (self.ttl_seconds, self.limit),
            )
            rows = await cursor.fetchall()
        return [row[0] for row in rows]

    async def drop_table(self) -> None:
        """測試清理用。"""
        async with await self._conn() as conn:
            await conn.execute(f"DROP TABLE IF EXISTS {self._table}")

    async def close(self) -> None:
        await self._pool.close()
