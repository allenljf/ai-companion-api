"""記憶體 TTL KV 測試（階段 6 儲存層的介面先行版，quiz-completions 24h 快取用）。"""

from app.storage.kv import InMemoryKV


def make_kv(now: list[float]) -> InMemoryKV:
    return InMemoryKV(clock=lambda: now[0])


def test_set_get_roundtrip():
    kv = InMemoryKV()
    kv.set("k", {"a": 1}, ttl_seconds=60)
    assert kv.get("k") == {"a": 1}


def test_missing_key_returns_none():
    assert InMemoryKV().get("nope") is None


def test_expired_key_returns_none():
    now = [1000.0]
    kv = make_kv(now)
    kv.set("k", "v", ttl_seconds=10)
    now[0] = 1009.9
    assert kv.get("k") == "v"
    now[0] = 1010.1
    assert kv.get("k") is None


def test_set_overwrites_value_and_ttl():
    now = [0.0]
    kv = make_kv(now)
    kv.set("k", "old", ttl_seconds=5)
    kv.set("k", "new", ttl_seconds=100)
    now[0] = 50.0
    assert kv.get("k") == "new"


def test_delete():
    kv = InMemoryKV()
    kv.set("k", "v", ttl_seconds=60)
    kv.delete("k")
    assert kv.get("k") is None
    kv.delete("k")  # 不存在也不炸
