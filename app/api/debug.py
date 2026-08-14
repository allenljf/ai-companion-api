"""階段 0 的平台實測端點（驗完可留著，路徑不在 App 契約內）。"""

import asyncio
import time

from fastapi import APIRouter, Query

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


@router.get("/sleep")
async def sleep(seconds: int = Query(default=45, ge=0, le=MAX_SLEEP_SECONDS)) -> dict:
    """驗證 hosting 的請求 timeout：45 秒的請求不能被平台切斷。"""
    started = time.monotonic()
    await asyncio.sleep(seconds)
    return success_envelope({"requested_seconds": seconds, "slept_seconds": round(time.monotonic() - started, 2)})
