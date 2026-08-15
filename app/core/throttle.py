"""Rate limiting（source-spec 2.4：per 路由獨立計數，防護打 LLM/產圖的成本）。

記憶體 sliding window：單 instance 部署下夠用（同 SQLite→記憶體 KV 的取捨）；
多副本時計數各自獨立、限流會寬鬆 N 倍——屆時換 KV 後端即可，介面不變。

原始服務踩過的坑：Laravel throttle signature 只含 domain+IP 不含路由，
所有 API 灌進同一個計數器。這裡強制每條路由帶自己的 scope。
"""

import time
from collections import deque
from typing import Callable

from fastapi import Request
from fastapi.responses import JSONResponse

WINDOW_SECONDS = 60.0

# 對照 source-spec 2.4 的 throttle 表
LIMITS = {
    "share_image_v2": 10,
    "self_introduction": 10,
    "from_orders": 10,
    "from_wish": 10,
    "from_history": 10,
    "travel_guide": 15,
    "travel_summary": 20,
    "travel_revise": 20,
    "recommend_city": 30,
}


class SlidingWindowLimiter:
    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._hits: dict[tuple[str, str], deque[float]] = {}

    def allow(self, scope: str, client: str, *, limit: int,
              window_seconds: float = WINDOW_SECONDS) -> bool:
        now = self._clock()
        hits = self._hits.setdefault((scope, client), deque())
        while hits and hits[0] <= now - window_seconds:
            hits.popleft()
        if len(hits) >= limit:
            return False
        hits.append(now)
        return True


_limiter = SlidingWindowLimiter()


class ThrottleExceeded(Exception):
    def __init__(self, scope: str):
        self.scope = scope


def client_ip(request: Request) -> str:
    """Cloud Run 在 proxy 之後：X-Forwarded-For 第一段是真實 client IP。"""
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def throttle(scope: str):
    """FastAPI dependency：超過限流拋 ThrottleExceeded（main.py handler 回 429）。"""
    limit = LIMITS[scope]

    async def dependency(request: Request) -> None:
        if not _limiter.allow(scope, client_ip(request), limit=limit):
            raise ThrottleExceeded(scope)

    return dependency


def throttle_response() -> JSONResponse:
    # 429：App 端契約是「退避後重試」（source-spec 6.5），body 格式不在契約內
    return JSONResponse(
        status_code=429,
        content={"metadata": {"status": "429001", "desc": ["Too Many Attempts."]}},
    )
