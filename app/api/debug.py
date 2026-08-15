"""平台實測端點（驗完可留著，路徑不在 App 契約內）。

- /debug/sleep、/debug/memory：階段 0 的 hosting 限制驗證
- /debug/storage：階段 6 的儲存層驗證（KV roundtrip + 併發鎖 + GCS 上傳公開讀）
"""

import asyncio
import time
import uuid

import anyio
from fastapi import APIRouter, Depends, Query

from app.api.deps import get_kv
from app.schemas.common import success_envelope

router = APIRouter(prefix="/debug", tags=["debug"])

MAX_SLEEP_SECONDS = 120


@router.get("/memory")
async def memory() -> dict:
    """回報容器記憶體使用量，驗證與平台上限（512Mi）的距離。"""
    info: dict = {}
    try:  # Linux 容器：cgroup v2
        info["cgroup_usage_mb"] = round(int(open("/sys/fs/cgroup/memory.current").read()) / 1024**2, 1)
        limit = open("/sys/fs/cgroup/memory.max").read().strip()
        info["cgroup_limit_mb"] = None if limit == "max" else round(int(limit) / 1024**2, 1)
    except OSError:
        pass
    try:  # 行程 RSS（Linux /proc；本機 mac 沒有就略過）
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    info["process_rss_mb"] = round(int(line.split()[1]) / 1024, 1)
    except OSError:
        pass
    return success_envelope(info)


@router.get("/storage")
async def storage_check(kv=Depends(get_kv)) -> dict:
    """階段 6 驗收：KV set/get/TTL、併發鎖原子性、GCS 上傳後公開 URL 可讀。"""
    report: dict = {"kv_backend": type(kv).__name__}

    # KV roundtrip + 過期語意
    key = f"debug:storage:{uuid.uuid4().hex}"
    await kv.set(key, {"ok": True}, ttl_seconds=60)
    report["kv_roundtrip"] = (await kv.get(key)) == {"ok": True}
    await kv.set(key + ":expired", "x", ttl_seconds=-1)
    report["kv_ttl_expired_hidden"] = (await kv.get(key + ":expired")) is None

    # 併發鎖：10 路並行只有一個拿到
    lock_key = key + ":lock"
    results = await asyncio.gather(*[kv.add(lock_key, i, ttl_seconds=30) for i in range(10)])
    report["lock_single_winner"] = results.count(True) == 1
    await kv.delete(lock_key)
    await kv.delete(key)

    # GCS 上傳 + 公開 URL 回讀（同步 client 丟 thread，避免卡 event loop）
    try:
        import httpx

        from app.storage.objects import upload_object

        object_key = f"debug/storage-check-{uuid.uuid4().hex}.txt"
        url = await anyio.to_thread.run_sync(
            lambda: upload_object(object_key, b"stage-6 check", content_type="text/plain")
        )
        async with httpx.AsyncClient(timeout=10) as http:
            response = await http.get(url)
        report["gcs_upload_public_read"] = (
            response.status_code == 200 and response.content == b"stage-6 check"
        )
        report["gcs_url"] = url
    except Exception as exc:
        report["gcs_upload_public_read"] = False
        report["gcs_error"] = f"{type(exc).__name__}: {exc}"

    return success_envelope(report)


@router.get("/sleep")
async def sleep(seconds: int = Query(default=45, ge=0, le=MAX_SLEEP_SECONDS)) -> dict:
    """驗證 hosting 的請求 timeout：45 秒的請求不能被平台切斷。"""
    started = time.monotonic()
    await asyncio.sleep(seconds)
    return success_envelope({"requested_seconds": seconds, "slept_seconds": round(time.monotonic() - started, 2)})
