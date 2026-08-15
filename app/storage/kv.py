"""KV 快取（階段 6 儲存層的介面先行版）。

目前只有記憶體實作，供 quiz-completions 的 24h 分析快取使用。
限制（記在 migration-plan 備註）：Cloud Run 重啟/縮容即遺失、多副本不共用——
階段 6 會以相同介面換成 Neon Postgres 實作，屆時 share-image（階段 8）也靠它跨請求取回分析。
"""

import time
from typing import Any, Callable


class InMemoryKV:
    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._store: dict[str, tuple[float, Any]] = {}

    def set(self, key: str, value: Any, ttl_seconds: float) -> None:
        self._store[key] = (self._clock() + ttl_seconds, value)

    def get(self, key: str) -> Any | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if self._clock() > expires_at:
            del self._store[key]
            return None
        return value

    def delete(self, key: str) -> None:
        self._store.pop(key, None)
