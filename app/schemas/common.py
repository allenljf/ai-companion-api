"""回應信封與錯誤契約（docs/source-spec.md 2.3）。

- 成功（含 LLM 軟失敗）→ HTTP 200 + metadata.status = "0000" + data
- request 驗證失敗 → HTTP 400 + metadata.desc 為訊息陣列，**沒有 data 欄位**
"""

from typing import Any

STATUS_SUCCESS = "0000"
STATUS_VALIDATION_ERROR = "110001"


def success_envelope(data: Any) -> dict:
    return {"metadata": {"status": STATUS_SUCCESS, "desc": "Success"}, "data": data}


def validation_error_envelope(messages: list[str]) -> dict:
    return {"metadata": {"status": STATUS_VALIDATION_ERROR, "desc": messages}}


def business_error_envelope(code: str, desc: str) -> dict:
    """Companion 業務錯誤（如 C007）：HTTP 200 + metadata.status=Cxxx、無 data 欄位。"""
    return {"metadata": {"status": code, "desc": desc}}
