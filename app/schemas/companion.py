"""Phase 1 request schemas（驗證規則對照 reference/requests/Quiz*/SelfIntroduction*.php）。"""

from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

from app.services.companion.partner import partner_tags


class QuizFetchRequest(BaseModel):
    # 題目出現次數 map（key 為 "{dimension_id}-{question_index}"）；present 但可為空 map
    shown_question_counts: dict[str, int]
    personality: str
    speech_style: str

    @model_validator(mode="after")
    def _counts_positive(self):
        for key, count in self.shown_question_counts.items():
            if count < 1:
                raise ValueError(f"The shown_question_counts.{key} must be at least 1.")
        return self


class QuizCompletionsRequest(BaseModel):
    completion_uuid: UUID
    personality: str
    speech_style: str
    selected_tags: list[str] = Field(..., min_length=1)
    # present 陣列（可為空）：需要排除的城市
    shown_cities: list[str]
    companion_name: str | None = Field(None, max_length=20)
    # App「捏頭像」產生的頭像網址，原樣保存供 quiz-gallery 顯示，不解析/不驗證來源
    partner_avatar_url: str | None = Field(None, max_length=500)


class ShareImageV2Request(BaseModel):
    completion_uuid: UUID
    # 原始服務用它做旅伴人物合成；Cloudflare 免費層不支援參考圖 → 接受但忽略（App 相容）
    partner_image_url: str | None = Field(None, max_length=500)


class SelfIntroductionRequest(BaseModel):
    companion_name: str = Field(..., min_length=1, max_length=20)
    personality: str
    speech_style: str
    gender: str

    # 動態比對 data/ai_partner.json 當下的選項清單（對照 PHP Rule::in + tagsFrom）
    @field_validator("personality")
    @classmethod
    def _personality_in_options(cls, v):
        if v not in partner_tags("personality"):
            raise ValueError("The selected personality is invalid.")
        return v

    @field_validator("speech_style")
    @classmethod
    def _speech_style_in_options(cls, v):
        if v not in partner_tags("speech_style"):
            raise ValueError("The selected speech_style is invalid.")
        return v

    @field_validator("gender")
    @classmethod
    def _gender_in_options(cls, v):
        if v not in partner_tags("gender"):
            raise ValueError("The selected gender is invalid.")
        return v
