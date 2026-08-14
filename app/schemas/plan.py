"""Phase 2 request schemas（驗證規則對照 reference/requests/*.php 與 reference/openapi/*.yaml）。

注意：這裡驗的是「App 端的 request body」，驗證失敗回 400 是契約的一部分；
LLM 輸出的 normalize（絕不拋錯）是另一回事，在 service/core 層處理。
"""

import re
from typing import Literal

from pydantic import BaseModel, Field, model_validator

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class OrderMaterial(BaseModel):
    prod_name: str | None = Field(None, max_length=200)
    package_name: str | None = Field(None, max_length=200)
    go_dt: str | None = None

    @model_validator(mode="after")
    def _check_go_dt(self):
        if self.go_dt is not None and not _DATE_RE.match(self.go_dt):
            raise ValueError("The order.go_dt field must match the format Y-m-d.")
        return self


class TravelSummaryRequest(BaseModel):
    entry_type: Literal["quiz_completion", "from_orders", "imported_itinerary", "from_zero"]

    # A/A2：權威城市（required_if）
    city: str | None = Field(None, max_length=50)

    # A：測驗結果材料
    city_image_url: str | None = Field(None, max_length=1000)
    intro_text: str | None = Field(None, max_length=2000)

    # A2：被點選那筆訂單的材料
    order: OrderMaterial | None = None

    # B：匯入行程
    source_type: Literal["text", "image"] | None = None
    content: str | None = Field(None, max_length=30000)
    image_urls: list[str] | None = Field(None, max_length=6)

    # A/B 共用：補充呼叫
    note: str | None = Field(None, max_length=1000)
    previous_summary: str | None = Field(None, max_length=4000)

    # 旅伴 persona
    companion_name: str | None = Field(None, max_length=20)
    personality: str | None = Field(None, max_length=30)
    speech_style: str | None = Field(None, max_length=30)

    @model_validator(mode="after")
    def _conditional_required(self):
        if self.entry_type in ("quiz_completion", "from_orders") and not (self.city or "").strip():
            raise ValueError("The city field is required.")

        if self.entry_type == "imported_itinerary":
            if self.source_type is None:
                raise ValueError("The source_type field is required.")
            if self.source_type == "text" and not (self.content or "").strip():
                raise ValueError("The content field is required.")
            if self.source_type == "image" and not self.image_urls:
                raise ValueError("The image_urls field is required.")

        return self
