from __future__ import annotations

from yogurt_social.continuity_memory import ContinuityAwareMemoryStore
from yogurt_social.conversation_state_observer import _with_current_continuity_context
from yogurt_social.conversation_state_runtime import ConversationStateRuntimeStore
from yogurt_social.response_planner import ResponsePlanner


def test_v43_phase_retires_v42_threads(tmp_path):
    db_path = tmp_path / "state.sqlite3"
    store = ConversationStateRuntimeStore(db_path)

    store.observe_user_turn(
        "owner",
        "session:v43",
        "开始实现 V4.2 Memory Recall Gate 的影子模式并运行测试",
    )
    state = store.observe_user_turn(
        "owner",
        "session:v43",
        "开始实现 V4.3 Unified Outbound Coordinator 的 shadow 影子模式",
    )

    assert state.current_topic == "统一发送协调"
    assert "实现 V4.2 记忆召回门控" not in state.open_threads
    assert "验证 V4.2 影子召回决策" not in state.open_threads
    assert "运行 V4.2 测试" not in state.open_threads
    assert "实现 V4.3 统一发送协调器" in state.open_threads
    assert "验证 V4.3 影子发送计划" in state.open_threads


def test_v43_completion_closes_only_matching_threads(tmp_path):
    db_path = tmp_path / "state.sqlite3"
    store = ConversationStateRuntimeStore(db_path)

    store.observe_user_turn(
        "owner",
        "session:v43-complete",
        "开始实现 V4.3 统一发送协调器，运行测试并更新文档",
    )
    state = store.observe_user_turn(
        "owner",
        "session:v43-complete",
        "V4.3 统一发送协调器已经实现，测试也通过了，文档也完成了",
    )

    assert state.current_topic == "统一发送协调"
    assert "实现 V4.3 统一发送协调器" not in state.open_threads
    assert "运行 V4.3 测试" not in state.open_threads
    assert "补全 V4.3 文档" not in state.open_threads
    assert "V4.3 统一发送协调器测试已通过" in state.recent_actions
    assert "V4.3 统一发送协调器已实现" in state.recent_actions


def test_v43_followups_inherit_active_phase_without_version_marker(tmp_path):
    store = ConversationStateRuntimeStore(tmp_path / "state.sqlite3")
    key = "session:v43-followup"

    store.observe_user_turn(
        "owner",
        key,
        "开始实现 V4.3.2 统一发送协调器加固并运行 Shadow 测试",
    )
    assistant_state = store.observe_assistant_turn(
        "owner",
        key,
        "alpha.6 本地自动化回归现在全部通过，接下来做真实微信 Shadow 测试。",
    )

    assert assistant_state.current_topic == "统一发送协调"
    assert "V4.3 统一发送协调器测试已通过" in assistant_state.recent_actions
    assert "V4.3 alpha.6 回归已通过" in assistant_state.recent_actions
    assert "V4.0 测试已通过" not in assistant_state.recent_actions

    followup = store.observe_user_turn(
        "owner",
        key,
        "接下来要测试什么",
    )
    assert followup.current_topic == "统一发送协调"
    assert "运行 V4.3 测试" in followup.open_threads
    assert "运行 V4.0 测试" not in followup.open_threads

    completed = store.observe_user_turn("owner", key, "通过了")
    assert "运行 V4.3 测试" not in completed.open_threads
    assert "V4.3 统一发送协调器测试已通过" in completed.recent_actions
    assert "V4.0 测试已通过" not in completed.recent_actions


def test_current_continuity_context_precedes_old_project_versions(tmp_path):
    store = ConversationStateRuntimeStore(tmp_path / "state.sqlite3")
    key = "session:v43-context"
    store.observe_user_turn(
        "owner",
        key,
        "开始 V4.3.2 统一发送协调器的 alpha.6 回归测试",
    )
    store.observe_assistant_turn(
        "owner",
        key,
        "alpha.6 本地自动化回归全部通过。",
    )
    message = "刚才解决了什么，接下来要测试什么"
    state = store.observe_user_turn("owner", key, message)
    planner = ResponsePlanner()
    context = _with_current_continuity_context(
        planner.build_context_block(planner.plan(message, state)),
        state,
    )

    assert context.startswith("[Yogurt Response Plan V4.1]")
    assert "[Yogurt Current Continuity]" in context
    assert "当前阶段：统一发送协调" in context
    assert "V4.3 alpha.6 回归已通过" in context
    assert "运行 V4.3 测试" in context
    assert "长期记忆中的旧项目版本" in context
    assert "V4.0 测试已通过" not in context


def test_current_project_progress_does_not_inject_old_versioned_episode(tmp_path):
    memory = ContinuityAwareMemoryStore(tmp_path / "memory.sqlite3")
    memory.add_episode(
        "owner",
        "我们刚刚成功搭建好了 Yogurt Social Layer V1，并完成了 OpenClaw 前置注入。",
        importance=1.0,
        source="test",
    )
    memory.add_fact(
        "owner",
        "model",
        "qwen3.5 9b",
        confidence=1.0,
        source="test",
    )

    current = memory.relevant(
        "owner",
        "刚才解决了什么，接下来要测试什么",
        limit=3,
    )
    assert all("Yogurt Social Layer V1" not in item.text for item in current)

    historical = memory.relevant(
        "owner",
        "之前 Yogurt Social Layer V1 做了什么",
        limit=3,
    )
    assert any("Yogurt Social Layer V1" in item.text for item in historical)
