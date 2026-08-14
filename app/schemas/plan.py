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


class PersonaFields(BaseModel):
    """旅伴 persona（同 Phase 1 慣例，供語氣個人化）。"""

    companion_name: str | None = Field(None, max_length=20)
    personality: str | None = Field(None, max_length=30)
    speech_style: str | None = Field(None, max_length=30)


class OrderInput(BaseModel):
    prod_name: str = Field(..., max_length=200)
    package_name: str | None = Field(None, max_length=200)
    destination_name: str | None = Field(None, max_length=100)


class TravelSummaryFromOrdersRequest(PersonaFields):
    # App 端已依出發日由近到遠排序，1~3 筆
    orders: list[OrderInput] = Field(..., min_length=1, max_length=3)


class ProductInput(BaseModel):
    prod_id: str = Field(..., max_length=50)
    prod_name: str = Field(..., max_length=200)
    introduction: str | None = Field(None, max_length=500)
    destination_names: list[str] | None = Field(None, max_length=5)

    @model_validator(mode="after")
    def _check_destination_name_lengths(self):
        for name in self.destination_names or []:
            if len(name) > 100:
                raise ValueError("The destination_names items may not be greater than 100 characters.")
        return self


class TravelSummaryFromWishRequest(PersonaFields):
    # 上限 20 是聚合摘要用途下的成本折衷（多筆商品共同推導最多 3 個城市）
    products: list[ProductInput] = Field(..., min_length=1, max_length=20)


class TravelSummaryFromHistoryRequest(PersonaFields):
    products: list[ProductInput] = Field(..., min_length=1, max_length=20)


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(..., max_length=2000)


class RecommendCityRequest(PersonaFields):
    # 無狀態設計：App 每次把完整對話歷史（含本次使用者輸入）全量傳入，
    # 後端以 role=user 的訊息數計算輪次（上限 5 輪）
    messages: list[ChatMessage] = Field(..., min_length=1, max_length=20)

    # 需排除的城市（已推薦過/使用者已去過），兼換城次數計數器
    shown_cities: list[str] | None = Field(None, max_length=30)

    @model_validator(mode="after")
    def _check_shown_city_lengths(self):
        for city in self.shown_cities or []:
            if len(city) > 50:
                raise ValueError("The shown_cities items may not be greater than 50 characters.")
        return self


class GuideOrderInput(BaseModel):
    """已預訂訂單（帶了就必須排入行程並以 oid 對映；oid 取 orders API 的訂單 id）。"""

    oid: str = Field(..., max_length=50)
    prod_name: str = Field(..., max_length=200)
    package_name: str | None = Field(None, max_length=200)
    destination_name: str | None = Field(None, max_length=100)
    go_dt: str | None = None

    @model_validator(mode="after")
    def _check_go_dt(self):
        if self.go_dt is not None and not _DATE_RE.match(self.go_dt):
            raise ValueError("The orders.go_dt field must match the format Y-m-d.")
        return self


class TravelGuideRequest(PersonaFields):
    # travel-summary 回傳的摘要，作為背景資訊帶入本次規劃
    summary: str = Field(..., max_length=4000)
    # 帶了 city 就是權威輸入：輸出城市必須一致，不一致視為失敗（技法 5）
    city: str | None = Field(None, max_length=50)

    # 旅遊偏好：map 形式、key/value 不固定（題目未來放 DCS 動態擴充），
    # 只限整體 key 數 ≤30，不對內容做結構驗證
    preferences: dict[str, object] | None = None

    orders: list[GuideOrderInput] | None = Field(None, max_length=3)
    # 來自 from-wish／from-history 被點選城市底下的 products；尚未購買，不推導 booked_anchor
    products: list[ProductInput] | None = Field(None, max_length=10)

    @model_validator(mode="after")
    def _check_preferences_size(self):
        if self.preferences is not None and len(self.preferences) > 30:
            raise ValueError("The preferences may not have more than 30 items.")
        return self


class ReviseDayInput(BaseModel):
    """單天骨架：內部結構刻意不深驗（彈性原則），缺欄位由 normalize 補齊；
    只擋異常膨脹的 items payload 灌進 LLM prompt。"""

    model_config = {"extra": "allow"}

    items: list | None = Field(None, max_length=50)


class TravelReviseRequest(PersonaFields):
    # 當前最新完整行程（App 每輪帶 merge 後的最新版），形狀 = travel-guide 的 itinerary_patch.days
    itinerary: list[ReviseDayInput] = Field(..., min_length=1, max_length=30)

    # 帶了 city 就是權威輸入：輸出必須一致，不一致視為失敗
    city: str | None = Field(None, max_length=50)

    # 優先只動這一天；未帶 = 整份可動
    target_day: int | None = Field(None, ge=1)

    # 整個聊天室從頭到尾的完整對話（同 recommend-city 做法），最後一則 = 本次修改需求
    messages: list[ChatMessage] = Field(..., min_length=1, max_length=100)

    preferences: dict[str, object] | None = None

    # 只需帶「要新排入」的材料；行程既有 id 自動併入白名單
    orders: list[GuideOrderInput] | None = Field(None, max_length=3)
    products: list[ProductInput] | None = Field(None, max_length=10)

    @model_validator(mode="after")
    def _check_preferences_size(self):
        if self.preferences is not None and len(self.preferences) > 30:
            raise ValueError("The preferences may not have more than 30 items.")
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
