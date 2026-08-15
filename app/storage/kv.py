"""KV 快取抽象（階段 6）：async get/set(TTL)/delete/add（原子搶鎖）。

兩種實作可切換（deps.get_kv 依 DATABASE_URL 決定）：
- InMemoryKV：本地開發/測試用；單 event loop 內操作天然原子
- PostgresKV（app/storage/pg.py）：Neon Postgres，跨請求/跨重啟持久，
  quiz-completions 24h 快取與階段 8 產圖併發鎖都靠它
"""

import time
from typing import Any, Callable


class InMemoryKV:
    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._store: dict[str, tuple[float, Any]] = {}

    async def set(self, key: str, value: Any, ttl_seconds: float) -> None:
        self._store[key] = (self._clock() + ttl_seconds, value)

    async def get(self, key: str) -> Any | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if self._clock() > expires_at:
            del self._store[key]
            return None
        return value

    async def delete(self, key: str) -> None:
        self._store.pop(key, None)

    async def add(self, key: str, value: Any, ttl_seconds: float) -> bool:
        """不存在或已過期才寫入（搶鎖語意）；拿到鎖回 True。

        單執行緒 event loop 內無 await 的區段天然原子，不需額外鎖。
        """
        entry = self._store.get(key)
        if entry is not None and self._clock() <= entry[0]:
            return False
        self._store[key] = (self._clock() + ttl_seconds, value)
        return True
