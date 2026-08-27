import sqlite3

from yogurt_social.memory import MemoryStore
from yogurt_social.memory_recall_gate import MemoryRecallGate
from yogurt_social.conversation_state_runtime import ConversationStateRuntimeStore
from yogurt_social.memory_shadow_audit import summarize_shadow_audit
from yogurt_social.memory_service.core import MemoryService, MemoryServiceConfig


def test_shadow_summary_is_aggregate_only_and_preserves_privacy(tmp_path):
    db = tmp_path / "memory.sqlite3"
    marker = "RAW-MESSAGE-MUST-NOT-APPEAR"
    MemoryStore(db).add_fact("owner", "model", "qwen3.5 9b")
    state = ConversationStateRuntimeStore(db).observe_user_turn("owner", "wechat:one", marker + " qwen3.5 9b")
    MemoryRecallGate(db).evaluate(user_id="owner", conversation_key="wechat:one", message=marker + " qwen3.5 9b", state=state)
    summary = summarize_shadow_audit(db)
    assert summary["turn_count"] == 1
    assert summary["surface_split"] == {"wechat": 1}
    assert summary["privacy"] == {"raw_message_exported": False, "raw_memory_text_exported": False}
    assert marker not in repr(summary)
    assert "candidate_kinds" in summary and "score_buckets" in summary
    with sqlite3.connect(db) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(memory_recall_events)")}
    assert not {"message", "prompt", "memory_text", "candidate_text"} & columns


def test_shadow_summary_supports_time_filters(tmp_path):
    db = tmp_path / "memory.sqlite3"
    MemoryStore(db).add_fact("owner", "model", "qwen3.5 9b")
    state = ConversationStateRuntimeStore(db).observe_user_turn("owner", "native:one", "qwen3.5 9b")
    MemoryRecallGate(db).evaluate(user_id="owner", conversation_key="native:one", message="qwen3.5 9b", state=state)
    assert summarize_shadow_audit(db, since="2100-01-01T00:00:00+00:00")["turn_count"] == 0


def test_low_information_pre_gate_writes_no_shadow_audit_event(tmp_path):
    service = MemoryService(MemoryServiceConfig(tmp_path / "memory.sqlite3"))
    result = service.recall(
        persona_id="yogurt", user_id="owner", surface="wechat",
        conversation_key="low-info", message="好的",
    )
    assert result["decision"] == "skip"
    assert summarize_shadow_audit(service.db_path)["turn_count"] == 0
