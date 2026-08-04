from __future__ import annotations

from dataclasses import replace

from yogurt_social.conversation_state_runtime import ConversationStateRuntimeStore
from yogurt_social.response_planner import ResponsePlanner


def test_v41_request_creates_planner_thread_and_project_plan(tmp_path):
    store = ConversationStateRuntimeStore(tmp_path / "memory.sqlite3")
    message = "开始 4.1 吧，先实现 Response Planner"
    state = store.observe_user_turn("owner", "session:v41", message)
    plan = ResponsePlanner().plan(message, state)

    assert state.current_topic == "回复规划器"
    assert state.open_threads == ("实现 V4.1 回复规划器",)
    assert plan.dialogue_act == "plan"
    assert plan.primary_goal == "advance_current_project"
    assert plan.reply_length == "medium"
    assert plan.bubble_target == 2
    assert plan.ask_followup is False
    assert "repeat_background" in plan.avoid
    assert plan.active_thread == "实现 V4.1 回复规划器"


def test_completion_is_celebration_and_closes_v41_thread(tmp_path):
    store = ConversationStateRuntimeStore(tmp_path / "memory.sqlite3")
    store.observe_user_turn(
        "owner",
        "session:v41",
        "开始实现 V4.1 回复规划器",
    )
    message = "V4.1 回复规划器已经实现，测试通过了"
    state = store.observe_user_turn("owner", "session:v41", message)
    plan = ResponsePlanner().plan(message, state)

    assert state.dialogue_act == "celebrate"
    assert "实现 V4.1 回复规划器" not in state.open_threads
    assert "V4.1 回复规划器已实现" in state.recent_actions
    assert "V4.1 回复规划器测试已通过" in state.recent_actions
    assert "V4.0 状态引擎已实现" not in state.recent_actions
    assert "V4.0 测试已通过" not in state.recent_actions
    assert plan.primary_goal == "acknowledge_achievement"
    assert plan.reply_length == "short"
    assert plan.sticker_preference == "celebration"
    assert plan.ask_followup is False


def test_comfort_plan_prioritizes_emotion_without_unsolicited_advice(tmp_path):
    store = ConversationStateRuntimeStore(tmp_path / "memory.sqlite3")
    message = "今天真的好累，还有点难过"
    state = store.observe_user_turn("owner", "session:comfort", message)
    plan = ResponsePlanner().plan(message, state)

    assert state.user_mood in {"sad", "tired"}
    assert plan.primary_goal == "validate_emotion"
    assert plan.reply_length == "short"
    assert plan.sticker_preference == "comfort"
    assert "unsolicited_advice" in plan.avoid
    assert "overplayful" in plan.avoid


def test_ambiguous_reference_allows_one_required_followup(tmp_path):
    store = ConversationStateRuntimeStore(tmp_path / "memory.sqlite3")
    message = "这个怎么弄"
    state = store.observe_user_turn("owner", "session:ambiguous", message)
    plan = ResponsePlanner().plan(message, state)

    assert plan.dialogue_act == "clarify"
    assert plan.primary_goal == "request_missing_detail"
    assert plan.ask_followup is True
    assert plan.bubble_target == 1
    assert plan.reply_length == "very_short"


def test_previous_long_reply_adds_repeat_guard(tmp_path):
    store = ConversationStateRuntimeStore(tmp_path / "memory.sqlite3")
    state = store.observe_user_turn("owner", "session:repeat", "开始实现 V4.1")
    state = replace(state, last_response_pattern="technical_long", state_version=3)
    message = "继续下一步"
    plan = ResponsePlanner().plan(message, state)

    assert plan.dialogue_act == "continue"
    assert plan.primary_goal == "continue_current_thread"
    assert plan.reply_length == "short"
    assert "repeat_long_summary" in plan.avoid
    assert "repeat_background" in plan.avoid


def test_context_is_bounded_and_contains_no_raw_message(tmp_path):
    store = ConversationStateRuntimeStore(tmp_path / "memory.sqlite3")
    raw_message = "开始 4.1，内部测试标记 RAW-SECRET-DO-NOT-COPY"
    state = store.observe_user_turn("owner", "session:privacy", raw_message)
    planner = ResponsePlanner()
    plan = planner.plan(raw_message, state)
    context = planner.build_context_block(plan)

    assert context.startswith("[Yogurt Response Plan V4.1]")
    assert len(context) <= planner.MAX_CONTEXT_CHARS
    assert raw_message not in context
    assert "RAW-SECRET-DO-NOT-COPY" not in context
    payload = plan.to_dict()
    assert "message" not in payload
    assert "raw_message" not in payload
    assert payload["continuity_topic"] == "回复规划器"


def test_acknowledgement_does_not_reopen_or_reexplain(tmp_path):
    store = ConversationStateRuntimeStore(tmp_path / "memory.sqlite3")
    store.observe_user_turn("owner", "session:ack", "开始实现 V4.1 回复规划器")
    message = "好的"
    state = store.observe_user_turn("owner", "session:ack", message)
    plan = ResponsePlanner().plan(message, state)

    assert plan.dialogue_act == "acknowledge"
    assert plan.primary_goal == "acknowledge_without_repeating"
    assert plan.reply_length == "very_short"
    assert plan.bubble_target == 1
    assert plan.sticker_preference == "none"
    assert "repeat_background" in plan.avoid
