from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.api import debug
from app.api.v1 import companion
from app.schemas.common import validation_error_envelope

app = FastAPI(title="AI Companion API", version="0.1.0")

app.include_router(companion.router)
app.include_router(debug.router)


@app.exception_handler(RequestValidationError)
async def handle_validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
    # 契約：驗證失敗回 400 + desc 訊息陣列，且沒有 data 欄位（source-spec 2.3）
    messages = []
    for err in exc.errors():
        loc = ".".join(str(p) for p in err["loc"] if p not in ("body", "query", "path"))
        messages.append(f"The {loc} field is invalid: {err['msg']}" if loc else err["msg"])
    return JSONResponse(status_code=400, content=validation_error_envelope(messages))


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
