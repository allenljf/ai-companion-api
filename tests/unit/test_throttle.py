"""Rate limiter 測試（sliding window、各 scope 獨立計數；source-spec 2.4）。"""

from app.core.throttle import SlidingWindowLimiter


def make(now: list[float]) -> SlidingWindowLimiter:
    return SlidingWindowLimiter(clock=lambda: now[0])


def test_allows_up_to_limit_then_blocks():
    now = [0.0]
    limiter = make(now)
    assert all(limiter.allow("s", "1.1.1.1", limit=3) for _ in range(3))
    assert limiter.allow("s", "1.1.1.1", limit=3) is False


def test_window_slides():
    now = [0.0]
    limiter = make(now)
    for _ in range(3):
        limiter.allow("s", "ip", limit=3)
    assert limiter.allow("s", "ip", limit=3) is False
    now[0] = 61.0  # 60 秒視窗滑出
    assert limiter.allow("s", "ip", limit=3) is True


def test_scopes_are_isolated():
    # 原始服務踩過的坑：throttle signature 不含路由 → 所有 API 灌同一個計數器。
    # 這裡每條路由帶自己的 scope 前綴隔離
    now = [0.0]
    limiter = make(now)
    for _ in range(3):
        assert limiter.allow("route_a", "ip", limit=3)
    assert limiter.allow("route_a", "ip", limit=3) is False
    assert limiter.allow("route_b", "ip", limit=3) is True


def test_clients_are_isolated():
    now = [0.0]
    limiter = make(now)
    for _ in range(3):
        limiter.allow("s", "1.1.1.1", limit=3)
    assert limiter.allow("s", "1.1.1.1", limit=3) is False
    assert limiter.allow("s", "2.2.2.2", limit=3) is True
