"""測試共用設定。"""

import pytest

from app.core.throttle import _limiter


@pytest.fixture(autouse=True)
def reset_throttle():
    """每個測試前清空限流計數——TestClient 的 client host 固定，跨測試會累積誤觸 429。"""
    _limiter._hits.clear()
    yield
