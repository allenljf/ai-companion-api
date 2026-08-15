import logging
import time

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.api import debug
from app.api.v1 import companion, plan
from app.core.throttle import ThrottleExceeded, throttle_response
from app.schemas.common import validation_error_envelope

logger = logging.getLogger("app.access")

app = FastAPI(
    title="AI Companion API",
    version="1.0.0",
    description=(
        "KKday「AI 旅伴」獨立雲端服務——Phase 1（旅伴建立/測驗/海報素材）+ "
        "Phase 2（行程規劃）共 17 支 API。\n\n"
        "共通契約：LLM 軟失敗一律回 HTTP 200 + `fail_reason` 有值 + 可渲染兜底內容；"
        "request 驗證失敗回 400（`metadata.status=110001`）；限流回 429。"
    ),
)

app.include_router(companion.router)
app.include_router(plan.router)
app.include_router(debug.router)


@app.exception_handler(RequestValidationError)
async def handle_validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
    # 契約：驗證失敗回 400 + desc 訊息陣列，且沒有 data 欄位（source-spec 2.3）
    messages = []
    for err in exc.errors():
        loc = ".".join(str(p) for p in err["loc"] if p not in ("body", "query", "path"))
        messages.append(f"The {loc} field is invalid: {err['msg']}" if loc else err["msg"])
    return JSONResponse(status_code=400, content=validation_error_envelope(messages))


@app.exception_handler(ThrottleExceeded)
async def handle_throttle(_: Request, exc: ThrottleExceeded) -> JSONResponse:
    logger.warning("throttled", extra={"scope": exc.scope})
    return throttle_response()


@app.middleware("http")
async def access_log(request: Request, call_next):
    """基本監控：每請求記 method/path/status/耗時（Cloud Run log-based metrics 可直接聚合）。"""
    started = time.monotonic()
    response = await call_next(request)
    logger.info(
        "request",
        extra={
            "method": request.method,
            "path": request.url.path,
            "status": response.status_code,
            "duration_ms": int((time.monotonic() - started) * 1000),
        },
    )
    return response


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
