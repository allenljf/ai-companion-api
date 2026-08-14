"""階段 0 的平台實測端點（驗完可留著，路徑不在 App 契約內）。"""

import asyncio
import time

from fastapi import APIRouter, Query

from app.schemas.common import success_envelope

router = APIRouter(prefix="/debug", tags=["debug"])

MAX_SLEEP_SECONDS = 120


@router.get("/sleep")
async def sleep(seconds: int = Query(default=45, ge=0, le=MAX_SLEEP_SECONDS)) -> dict:
    """驗證 hosting 的請求 timeout：45 秒的請求不能被平台切斷。"""
    started = time.monotonic()
    await asyncio.sleep(seconds)
    return success_envelope({"requested_seconds": seconds, "slept_seconds": round(time.monotonic() - started, 2)})
