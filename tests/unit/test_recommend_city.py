"""recommend-city：輪次控制 + shown_cities 違規重試（source-spec 5.5 / 8.9）。"""

import json

import pytest

from app.services.llm.client import LLMClient, LLMProvider
from app.services.plan.recommend_city import (
    MAX_CITY_SWAPS,
    MAX_ROUNDS,
    build_system_prompt,
    build_user_message,
    count_rounds,
    is_shown_city,
    recommend,
)


class TestCountRounds:
    def test_counts_only_user_messages(self):
        messages = [
            {"role": "user", "content": "想去哪玩"},
            {"role": "assistant", "content": "想放鬆還是探險？"},
            {"role": "user", "content": "放鬆"},
        ]
        assert count_rounds(messages) == 2

    def test_ignores_unknown_roles(self):
        assert count_rounds([{"role": "system", "content": "x"}, {"content": "y"}]) == 0


class TestIsShownCity:
    def test_literal_match_trim_case_insensitive(self):
        assert is_shown_city("京都", ["東京", "京都"])
        assert is_shown_city("Kyoto", ["kyoto"])
        assert is_shown_city("kyoto", ["  Kyoto  "])
        assert not is_shown_city("大阪", ["東京", "京都"])

    def test_cross_language_not_matched_literally(self):
        # 跨語系同城（京都 vs Kyoto）靠 prompt 禁令，不在字面比對範圍
        assert not is_shown_city("Kyoto", ["京都"])


class TestBuildUserMessage:
    def test_conversation_and_shown_cities(self):
        msg = build_user_message(
            [{"role": "user", "content": "想看海"}], ["沖繩"]
        )
        payload = json.loads(msg[len("<<<USER_INPUT\n") : -len("\n>>>END_USER_INPUT")])
        assert payload == {
            "conversation": [{"role": "user", "content": "想看海"}],
            "shown_cities": ["沖繩"],
        }

    def test_no_shown_cities_key_when_empty(self):
        msg = build_user_message([{"role": "user", "content": "hi"}], [])
        payload = json.loads(msg[len("<<<USER_INPUT\n") : -len("\n>>>END_USER_INPUT")])
        assert "shown_cities" not in payload


class TestBuildSystemPrompt:
    def test_round_1_free_convergence(self):
        prompt = build_system_prompt({}, round_=1, shown_cities=[], violated_city=None)
        assert "【本輪指令：自由收斂】這是第 1 輪、還剩 4 輪" in prompt
        assert "城市禁令" not in prompt
        assert "重要修正" not in prompt

    def test_round_4_reminder(self):
        prompt = build_system_prompt({}, round_=4, shown_cities=[], violated_city=None)
        assert "【本輪指令：倒數提醒】這是第 4 輪、只剩 1 輪" in prompt
        assert "下一次輸入完，你就會直接幫他選出城市" in prompt

    def test_round_5_forced(self):
        prompt = build_system_prompt({}, round_=5, shown_cities=[], violated_city=None)
        assert "【本輪指令：強制收斂（最後一輪）】這是第 5 輪" in prompt
        assert "recommended_city 必填，不得為空字串" in prompt
        assert "不得再提出任何問題" in prompt

    def test_round_beyond_5_still_forced(self):
        prompt = build_system_prompt({}, round_=7, shown_cities=[], violated_city=None)
        assert "強制收斂" in prompt

    def test_ban_section_injected_with_shown_cities(self):
        prompt = build_system_prompt({}, round_=2, shown_cities=["東京", "京都"], violated_city=None)
        assert "## 城市禁令（最高優先級，違反即為失敗）" in prompt
        assert "東京、京都" in prompt

    def test_forced_round_restates_ban_at_end_position(self):
        # 禁令必須在強制收斂指令內重申（結尾位置）——sit 實測「必須給城市」會壓過中段禁令
        prompt = build_system_prompt({}, round_=5, shown_cities=["東京"], violated_city=None)
        instruction_pos = prompt.index("強制收斂（最後一輪）")
        restated = prompt.index("絕對不可是城市禁令清單中的任何城市", instruction_pos)
        assert restated > instruction_pos

    def test_violation_section_at_very_end(self):
        prompt = build_system_prompt({}, round_=5, shown_cities=["東京"], violated_city="東京")
        assert "## ⚠️ 重要修正（你上一次回答違規了）" in prompt
        assert "你上一次推薦了「東京」" in prompt
        # 違規回饋必須是 prompt 最結尾的段落（技法 7：結尾指令最強）
        assert prompt.rstrip().endswith("改推一個清單外、同樣符合使用者偏好的城市。")

    def test_persona_injected(self):
        prompt = build_system_prompt(
            {"companion_name": "阿旅", "personality": "幽默"},
            round_=1, shown_cities=[], violated_city=None,
        )
        assert "你的名字是「阿旅」，人格特質與說話風格關鍵字：幽默" in prompt


class SequenceProvider(LLMProvider):
    """依序回傳 replies（支援 Exception）；記錄每次呼叫的 system prompt。"""

    def __init__(self, replies: list):
        self.replies = list(replies)
        self.calls: list[dict] = []

    async def chat(self, system, user, *, max_tokens, model, content_parts=None, json_mode=False, reasoning_effort=None):
        self.calls.append({"system": system, "user": user, "max_tokens": max_tokens})
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def make_client(provider) -> LLMClient:
    return LLMClient(providers={"p": provider}, routing={"recommend_city": ("p", "test-model")})


def user_rounds(n: int) -> list[dict]:
    """組出 n 輪的對話歷史（user/assistant 交錯）。"""
    messages = []
    for i in range(n):
        messages.append({"role": "user", "content": f"第{i + 1}句"})
        if i < n - 1:
            messages.append({"role": "assistant", "content": f"回{i + 1}"})
    return messages


NOT_CONVERGED = '{"reply": "想去放鬆還是探險？", "quick_replies": ["放鬆", "探險"], "recommended_city": "", "city_reason": "", "off_topic": false}'
CONVERGED_OKINAWA = '{"reply": "推薦沖繩！", "quick_replies": ["就去這裡"], "recommended_city": "沖繩", "city_reason": "海景放鬆首選", "off_topic": false}'


class TestRecommend:
    @pytest.mark.asyncio
    async def test_round_fields_and_not_final(self):
        provider = SequenceProvider([NOT_CONVERGED])
        result = await recommend({"messages": user_rounds(2)}, make_client(provider))

        assert result["round"] == 2
        assert result["max_rounds"] == MAX_ROUNDS
        assert result["remaining_rounds"] == 3
        assert result["is_final"] is False
        assert result["recommended_city"] == ""
        assert result["quick_replies"] == ["放鬆", "探險"]  # 未收斂：用 LLM 的
        assert result["swap_limit_reached"] is False
        assert result["fail_reason"] is None
        assert provider.calls[0]["max_tokens"] == 1000

    @pytest.mark.asyncio
    async def test_converged_chips_overridden_by_backend(self):
        provider = SequenceProvider([CONVERGED_OKINAWA])
        result = await recommend({"messages": user_rounds(2)}, make_client(provider))

        assert result["is_final"] is True
        assert result["recommended_city"] == "沖繩"
        assert result["city_reason"] == "海景放鬆首選"
        # chips 後端強制覆寫，不受 LLM 影響
        assert result["quick_replies"] == ["就去沖繩！", "換一個城市", "重新聊聊"]

    @pytest.mark.asyncio
    async def test_converged_at_swap_cap_drops_swap_chip(self):
        provider = SequenceProvider([CONVERGED_OKINAWA])
        shown = [f"城市{i}" for i in range(MAX_CITY_SWAPS)]  # 剛好 5 個 = 最後一次換城
        result = await recommend(
            {"messages": user_rounds(2), "shown_cities": shown}, make_client(provider)
        )
        assert result["quick_replies"] == ["就去沖繩！", "重新聊聊"]

    @pytest.mark.asyncio
    async def test_swap_limit_exceeded_no_llm_call(self):
        provider = SequenceProvider([])  # 任何呼叫都會 IndexError → 證明沒打 LLM
        shown = [f"城市{i}" for i in range(6)]  # >5
        result = await recommend(
            {"messages": user_rounds(1), "shown_cities": shown}, make_client(provider)
        )
        assert result["swap_limit_reached"] is True
        assert result["quick_replies"] == ["重新聊聊"]
        assert result["ai_model"] is None
        assert "已經幫你換過 5 個城市啦" in result["reply"]
        assert result["fail_reason"] is None
        assert provider.calls == []

    @pytest.mark.asyncio
    async def test_forced_round_without_city_is_failure(self):
        provider = SequenceProvider([NOT_CONVERGED])
        result = await recommend({"messages": user_rounds(5)}, make_client(provider))
        assert result["fail_reason"] is not None
        assert "強制收斂" in result["fail_reason"]
        assert result["reply"] == "嗯…我這邊卡了一下，再說一次剛剛那句好嗎？"
        assert result["is_final"] is False

    @pytest.mark.asyncio
    async def test_llm_exception_is_soft_failure(self):
        provider = SequenceProvider([RuntimeError("boom")])
        result = await recommend({"messages": user_rounds(1)}, make_client(provider))
        assert "RuntimeError" in result["fail_reason"]
        assert result["round"] == 1
        assert result["ai_model"] == "test-model"

    @pytest.mark.asyncio
    async def test_violation_triggers_retry_with_feedback_at_end(self):
        violating = '{"reply": "推京都！", "quick_replies": [], "recommended_city": "京都", "city_reason": "x", "off_topic": false}'
        provider = SequenceProvider([violating, CONVERGED_OKINAWA])
        result = await recommend(
            {"messages": user_rounds(2), "shown_cities": ["京都"]}, make_client(provider)
        )
        assert result["fail_reason"] is None
        assert result["recommended_city"] == "沖繩"
        assert len(provider.calls) == 2
        # 重試的 prompt 要有違規回饋且在最結尾
        retry_prompt = provider.calls[1]["system"]
        assert "你上一次推薦了「京都」" in retry_prompt
        assert retry_prompt.rstrip().endswith("改推一個清單外、同樣符合使用者偏好的城市。")

    @pytest.mark.asyncio
    async def test_retry_still_violating_is_failure(self):
        violating = '{"reply": "推京都！", "quick_replies": [], "recommended_city": "京都", "city_reason": "x", "off_topic": false}'
        provider = SequenceProvider([violating, violating])
        result = await recommend(
            {"messages": user_rounds(2), "shown_cities": ["京都"]}, make_client(provider)
        )
        assert "重試後推薦城市仍與 shown_cities 重複" in result["fail_reason"]
        assert result["recommended_city"] == ""

    @pytest.mark.asyncio
    async def test_retry_call_exception_is_failure(self):
        violating = '{"reply": "推京都！", "quick_replies": [], "recommended_city": "京都", "city_reason": "x", "off_topic": false}'
        provider = SequenceProvider([violating, RuntimeError("down")])
        result = await recommend(
            {"messages": user_rounds(2), "shown_cities": ["京都"]}, make_client(provider)
        )
        assert result["fail_reason"] is not None

    @pytest.mark.asyncio
    async def test_off_topic_passthrough_and_round_still_counted(self):
        off = '{"reply": "我們聊旅遊吧！", "quick_replies": [], "recommended_city": "", "city_reason": "", "off_topic": true}'
        provider = SequenceProvider([off])
        result = await recommend({"messages": user_rounds(3)}, make_client(provider))
        assert result["off_topic"] is True
        assert result["round"] == 3  # 離題照樣計數

    @pytest.mark.asyncio
    async def test_quick_replies_capped_at_4(self):
        many = '{"reply": "選一個吧", "quick_replies": ["a","b","c","d","e","f"], "recommended_city": "", "city_reason": "", "off_topic": false}'
        provider = SequenceProvider([many])
        result = await recommend({"messages": user_rounds(1)}, make_client(provider))
        assert result["quick_replies"] == ["a", "b", "c", "d"]

    @pytest.mark.asyncio
    async def test_shown_cities_blank_entries_filtered(self):
        # 空白項過濾後只剩 5 個 → 不觸發 >5 保底，仍打 LLM
        provider = SequenceProvider([CONVERGED_OKINAWA])
        shown = ["A", "B", "C", "D", "E", "  ", ""]
        result = await recommend(
            {"messages": user_rounds(1), "shown_cities": shown}, make_client(provider)
        )
        assert result["swap_limit_reached"] is False
        assert result["recommended_city"] == "沖繩"


class TestTraditionalChinesePostProcess:
    @pytest.mark.asyncio
    async def test_simplified_output_converted(self):
        simplified = '{"reply": "没关系，推荐冲绳！", "quick_replies": ["躺平发呆"], "recommended_city": "冲绳", "city_reason": "预算刚好", "off_topic": false}'
        provider = SequenceProvider([simplified])
        result = await recommend({"messages": user_rounds(2)}, make_client(provider))

        assert result["reply"] == "沒關係，推薦沖繩！"
        assert result["recommended_city"] == "沖繩"
        assert result["city_reason"] == "預算剛好"
        # 收斂 chips 由後端組（城市名已轉繁）
        assert result["quick_replies"][0] == "就去沖繩！"

    @pytest.mark.asyncio
    async def test_unconverged_quick_replies_converted(self):
        simplified = '{"reply": "想去哪儿？", "quick_replies": ["看海边", "逛庙会"], "recommended_city": "", "city_reason": "", "off_topic": false}'
        provider = SequenceProvider([simplified])
        result = await recommend({"messages": user_rounds(1)}, make_client(provider))
        assert result["quick_replies"] == ["看海邊", "逛廟會"]
