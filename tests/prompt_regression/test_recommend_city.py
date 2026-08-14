"""recommend-city 的 prompt 回歸測試（真實 LLM，手動執行）。

執行：pytest -m prompt_regression tests/prompt_regression/test_recommend_city.py
重點驗證 migration-plan 6.4：第 5 輪必給城市；給的城市不在 shown_cities。
"""

import pytest

from app.api.deps import get_llm_client
from app.services.plan.recommend_city import recommend

pytestmark = pytest.mark.prompt_regression


@pytest.fixture(autouse=True)
def fresh_llm_client():
    get_llm_client.cache_clear()
    yield
    get_llm_client.cache_clear()


@pytest.mark.asyncio
async def test_round_1_asks_a_question_with_chips():
    result = await recommend(
        {"messages": [{"role": "user", "content": "想出去玩，但還沒有想法"}]},
        get_llm_client(),
    )
    assert result["fail_reason"] is None
    assert result["reply"]
    assert result["round"] == 1
    # 第 1 輪通常提問收斂偏好；若提前收斂也合法，但未收斂時 chips 要有 2~4 個
    if not result["is_final"]:
        assert 2 <= len(result["quick_replies"]) <= 4


@pytest.mark.asyncio
async def test_round_5_forced_convergence_respects_ban_list():
    # 弱模型最容易在這裡同時違反「必須給城市」和「不能給清單內城市」（migration-plan 階段 3）
    messages = [
        {"role": "user", "content": "想出去玩"},
        {"role": "assistant", "content": "想放鬆還是探險？"},
        {"role": "user", "content": "放鬆看海"},
        {"role": "assistant", "content": "預算多少？"},
        {"role": "user", "content": "3萬台幣"},
        {"role": "assistant", "content": "想吃什麼？"},
        {"role": "user", "content": "海鮮"},
        {"role": "assistant", "content": "最後一個問題：想去多久？"},
        {"role": "user", "content": "5天"},
    ]
    shown = ["沖繩", "普吉島", "峴港", "長灘島"]
    result = await recommend(
        {"messages": messages, "shown_cities": shown}, get_llm_client()
    )
    assert result["fail_reason"] is None
    assert result["round"] == 5
    assert result["is_final"] is True
    assert result["recommended_city"] != ""
    assert result["recommended_city"] not in shown
    assert result["city_reason"]
    # 收斂 chips 由後端固定
    assert result["quick_replies"][0] == f"就去{result['recommended_city']}！"
    assert "換一個城市" in result["quick_replies"]


@pytest.mark.asyncio
async def test_swap_city_keeps_preferences_without_new_questions():
    messages = [
        {"role": "user", "content": "想看海放空"},
        {"role": "assistant", "content": "推薦沖繩！海景放鬆首選。"},
        {"role": "user", "content": "換一個城市"},
    ]
    result = await recommend(
        {"messages": messages, "shown_cities": ["沖繩"]}, get_llm_client()
    )
    assert result["fail_reason"] is None
    # 換城時必須直接給新城市（規則 6：不再重新提問）
    assert result["is_final"] is True
    assert result["recommended_city"] not in ("", "沖繩")


@pytest.mark.asyncio
async def test_off_topic_redirected_and_flagged():
    result = await recommend(
        {"messages": [{"role": "user", "content": "幫我寫一段 Python 快速排序"}]},
        get_llm_client(),
    )
    assert result["fail_reason"] is None
    assert result["off_topic"] is True
    assert "def " not in result["reply"]  # 不接話
