"""self-introduction 服務測試（source-spec 4.7，mock LLM）。純文字輸出（非 JSON）。"""

import pytest

from app.services.companion.self_introduction import generate_self_introduction
from app.services.llm.client import LLMClient, LLMProvider

BASE_PARAMS = {
    "companion_name": "阿旅",
    "personality": "humorous",
    "speech_style": "friendly",
    "gender": "female",
}


class StubProvider(LLMProvider):
    def __init__(self, reply):
        self.reply = reply
        self.calls: list[dict] = []

    async def chat(self, system, user, *, max_tokens, model, content_parts=None, json_mode=False, reasoning_effort=None):
        self.calls.append(
            {"system": system, "user": user, "max_tokens": max_tokens, "json_mode": json_mode}
        )
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


def make_client(reply) -> tuple[LLMClient, StubProvider]:
    provider = StubProvider(reply)
    return LLMClient(
        providers={"stub": provider}, routing={"self_introduction": ("stub", "intro-model")}
    ), provider


@pytest.mark.asyncio
async def test_success_returns_trimmed_introduction():
    client, provider = make_client("  嗨嗨！我是阿旅，準備好出發了嗎？  ")
    result = await generate_self_introduction(dict(BASE_PARAMS), client)
    assert result["introduction"] == "嗨嗨！我是阿旅，準備好出發了嗎？"
    assert result["ai_model"] == "intro-model"
    assert result["fail_reason"] is None
    # 純文字輸出：不可開 json_mode
    assert provider.calls[0]["json_mode"] is False
    assert provider.calls[0]["max_tokens"] == 800
    # persona 與性別解析為人類可讀文字進 prompt
    user = provider.calls[0]["user"]
    assert "阿旅" in user and "女" in user
    assert "幽默風趣" in user and "好朋友" in user


@pytest.mark.asyncio
async def test_llm_exception_falls_back_with_fail_reason():
    client, _ = make_client(RuntimeError("boom"))
    result = await generate_self_introduction(dict(BASE_PARAMS), client)
    assert result["fail_reason"] is not None and "boom" in result["fail_reason"]
    assert "阿旅" in result["introduction"]  # fallback 固定文案含旅伴名字
    assert result["introduction"].startswith("嗨！我是阿旅")


@pytest.mark.asyncio
async def test_empty_reply_falls_back_with_specific_reason():
    client, _ = make_client("   ")
    result = await generate_self_introduction(dict(BASE_PARAMS), client)
    assert result["fail_reason"] == "LLM fail: LLM 回應為空字串"
    assert "阿旅" in result["introduction"]


@pytest.mark.asyncio
async def test_simplified_output_converted():
    client, _ = make_client("我是阿旅，预算控管交给我")
    result = await generate_self_introduction(dict(BASE_PARAMS), client)
    assert "預算" in result["introduction"]


@pytest.mark.asyncio
async def test_system_prompt_uses_partner_intro_prompt_from_data():
    # data/ai_partner.json 的 partner_intro_prompt 優先於程式內建 fallback
    client, provider = make_client("好的")
    await generate_self_introduction(dict(BASE_PARAMS), client)
    assert "自我介紹文案引擎" in provider.calls[0]["system"]
