from __future__ import annotations

import sqlite3

from yogurt_social.conversation_state_runtime import ConversationStateRuntimeStore
from yogurt_social.memory import MemoryStore
from yogurt_social.memory_recall_gate import MemoryRecallGate


def _state(db_path, user_id: str, conversation_key: str, message: str):
    store = ConversationStateRuntimeStore(db_path)
    return store.observe_user_turn(user_id, conversation_key, message)


def test_directly_relevant_stable_fact_is_allowed(tmp_path):
    db_path = tmp_path / "memory.sqlite3"
    memory = MemoryStore(db_path)
    memory.add_fact("owner", "model", "qwen3.5 9b", confidence=1.0)
    message = "qwen3.5 9b 这个模型的上下文应该怎么设置"
    state = _state(db_path, "owner", "session:model", message)

    decision = MemoryRecallGate(db_path).evaluate(
        user_id="owner",
        conversation_key="session:model",
        message=message,
        state=state,
    )

    assert decision.decision == "allow"
    assert decision.candidate is not None
    assert decision.candidate.layer == "stable"
    assert decision.candidate.kind == "fact"
    assert decision.candidate.memory_key == "model"
    assert decision.candidate.score >= decision.threshold
    assert "gate_allow" in decision.reasons


def test_unrelated_memory_is_blocked_by_relevance(tmp_path):
    db_path = tmp_path / "memory.sqlite3"
    memory = MemoryStore(db_path)
    memory.add_fact("owner", "model", "qwen3.5 9b", confidence=1.0)
    message = "重庆今天会不会下雨"
    state = _state(db_path, "owner", "session:weather", message)

    decision = MemoryRecallGate(db_path).evaluate(
        user_id="owner",
        conversation_key="session:weather",
        message=message,
        state=state,
    )

    assert decision.decision == "blocked"
    assert "relevance_below_threshold" in decision.reasons
    assert "gate_block" in decision.reasons


def test_acknowledgement_does_not_trigger_recall(tmp_path):
    db_path = tmp_path / "memory.sqlite3"
    memory = MemoryStore(db_path)
    memory.add_preference(
        "owner",
        "assistant_behavior",
        "回复简短自然",
        confidence=1.0,
    )
    message = "好的"
    state = _state(db_path, "owner", "session:ack", message)

    decision = MemoryRecallGate(db_path).evaluate(
        user_id="owner",
        conversation_key="session:ack",
        message=message,
        state=state,
    )

    assert decision.decision == "blocked"
    assert "low_information_turn" in decision.reasons


def test_tired_user_is_not_interrupted_by_unsolicited_memory(tmp_path):
    db_path = tmp_path / "memory.sqlite3"
    memory = MemoryStore(db_path)
    memory.add_fact("owner", "model", "qwen3.5 9b", confidence=1.0)
    message = "今天好累，不过 qwen3.5 9b 的配置还没弄完"
    state = _state(db_path, "owner", "session:tired", message)

    decision = MemoryRecallGate(db_path).evaluate(
        user_id="owner",
        conversation_key="session:tired",
        message=message,
        state=state,
    )

    assert state.user_mood == "tired"
    assert decision.decision == "blocked"
    assert "emotion_non_interruption" in decision.reasons


def test_cooldown_blocks_repeated_unsolicited_recall(tmp_path):
    db_path = tmp_path / "memory.sqlite3"
    memory = MemoryStore(db_path)
    memory.add_fact("owner", "model", "qwen3.5 9b", confidence=1.0)
    gate = MemoryRecallGate(
        db_path,
        cooldown_turns=8,
        same_memory_cooldown_turns=20,
    )
    message = "qwen3.5 9b 模型的上下文长度怎么调整"

    first_state = _state(db_path, "owner", "session:cooldown", message)
    first = gate.evaluate(
        user_id="owner",
        conversation_key="session:cooldown",
        message=message,
        state=first_state,
    )
    second_state = _state(db_path, "owner", "session:cooldown", message)
    second = gate.evaluate(
        user_id="owner",
        conversation_key="session:cooldown",
        message=message,
        state=second_state,
    )

    assert first.decision == "allow"
    assert second.decision == "blocked"
    assert "conversation_cooldown" in second.reasons
    assert "same_memory_cooldown" in second.reasons
    assert second.cooldown_remaining > 0
    assert second.same_memory_cooldown_remaining > 0


def test_explicit_recall_request_can_override_cooldown(tmp_path):
    db_path = tmp_path / "memory.sqlite3"
    memory = MemoryStore(db_path)
    memory.add_fact("owner", "school", "哥大 BME", confidence=1.0)
    gate = MemoryRecallGate(db_path, cooldown_turns=8, same_memory_cooldown_turns=20)
    first_message = "哥大 BME 的课程安排怎么规划"
    first_state = _state(db_path, "owner", "session:explicit", first_message)
    first = gate.evaluate(
        user_id="owner",
        conversation_key="session:explicit",
        message=first_message,
        state=first_state,
    )
    second_message = "你还记得我在哥大 BME 吗"
    second_state = _state(db_path, "owner", "session:explicit", second_message)
    second = gate.evaluate(
        user_id="owner",
        conversation_key="session:explicit",
        message=second_message,
        state=second_state,
    )

    assert first.decision == "allow"
    assert second.explicit_request is True
    assert second.decision == "allow"
    assert "explicit_recall_request" in second.reasons


def test_high_risk_topic_blocks_unsolicited_recall(tmp_path):
    db_path = tmp_path / "memory.sqlite3"
    memory = MemoryStore(db_path)
    memory.add_fact("owner", "school", "哥大 BME", confidence=1.0)
    message = "哥大 BME 的签证材料该怎么准备"
    state = _state(db_path, "owner", "session:visa", message)

    decision = MemoryRecallGate(db_path).evaluate(
        user_id="owner",
        conversation_key="session:visa",
        message=message,
        state=state,
    )

    assert decision.decision == "blocked"
    assert "high_risk_topic" in decision.reasons


def test_memory_candidates_are_isolated_by_user(tmp_path):
    db_path = tmp_path / "memory.sqlite3"
    memory = MemoryStore(db_path)
    memory.add_fact("owner", "model", "qwen3.5 9b", confidence=1.0)
    message = "qwen3.5 9b 模型怎么设置"
    state = _state(db_path, "friend", "session:friend", message)

    decision = MemoryRecallGate(db_path).evaluate(
        user_id="friend",
        conversation_key="session:friend",
        message=message,
        state=state,
    )

    assert decision.decision == "none"
    assert decision.candidate is None
    assert "no_candidate" in decision.reasons


def test_recall_audit_contains_no_raw_message_or_memory_text(tmp_path):
    db_path = tmp_path / "memory.sqlite3"
    memory = MemoryStore(db_path)
    secret_marker = "RAW-MEMORY-TEXT-MUST-NOT-BE-COPIED"
    raw_message = f"你还记得 {secret_marker} 吗"
    memory.add_fact("owner", "marker", secret_marker, confidence=1.0)
    state = _state(db_path, "owner", "session:privacy", raw_message)
    gate = MemoryRecallGate(db_path)

    gate.evaluate(
        user_id="owner",
        conversation_key="session:privacy",
        message=raw_message,
        state=state,
    )
    events = gate.events(
        user_id="owner",
        conversation_key="session:privacy",
        limit=10,
    )

    assert len(events) == 1
    serialized = repr(events)
    assert raw_message not in serialized
    assert secret_marker not in serialized
    with sqlite3.connect(db_path) as connection:
        columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(memory_recall_events)"
            ).fetchall()
        }
    assert "message" not in columns
    assert "prompt" not in columns
    assert "memory_text" not in columns
    assert "candidate_text" not in columns


def test_v42_phase_retires_v41_implementation_threads(tmp_path):
    db_path = tmp_path / "memory.sqlite3"
    store = ConversationStateRuntimeStore(db_path)
    store.observe_user_turn(
        "owner",
        "session:v42",
        "开始实现 V4.1 回复规划器并运行测试",
    )
    state = store.observe_user_turn(
        "owner",
        "session:v42",
        "开始实现 V4.2 Memory Recall Gate 的影子模式",
    )

    assert state.current_topic == "记忆召回门控"
    assert "实现 V4.1 回复规划器" not in state.open_threads
    assert "运行 V4.1 测试" not in state.open_threads
    assert "实现 V4.2 记忆召回门控" in state.open_threads
    assert "验证 V4.2 影子召回决策" in state.open_threads
