from __future__ import annotations

from yogurt_social.conversation_state_runtime import ConversationStateRuntimeStore


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
