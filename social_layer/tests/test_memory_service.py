import sqlite3

from yogurt_social.memory_service.client import MemoryServiceClient
from yogurt_social.memory_service.core import MemoryService, MemoryServiceConfig


def _service(tmp_path):
    return MemoryService(MemoryServiceConfig(tmp_path / "memory.sqlite3"))


def test_shared_long_term_memory_and_isolated_surface_state(tmp_path):
    service = _service(tmp_path)
    service.observe(
        persona_id="yogurt", user_id="owner", surface="wechat",
        conversation_key="friend-a", phase="user", user_message="我的显卡是 RTX 4080",
    )
    written = service.observe(
        persona_id="yogurt", user_id="owner", surface="wechat",
        conversation_key="friend-a", phase="assistant", user_message="我的显卡是 RTX 4080",
        assistant_reply="收到。",
    )
    assert written["promoted_count"] == 1

    result = service.recall(
        persona_id="yogurt", user_id="owner", surface="native",
        conversation_key="desktop", message="我那台跑 AI 的显卡是什么？",
    )
    assert result["decision"] == "allow"
    assert result["memories"][0]["text"] == "gpu=RTX 4080"

    assert service.states.get("owner", "wechat:friend-a") is not None
    assert service.states.get("owner", "native:desktop") is not None
    assert service.states.get("owner", "wechat:desktop") is None


def test_derived_fts_never_returns_replaced_canonical_fact(tmp_path):
    service = _service(tmp_path)
    service.memory.add_fact("owner", "gpu", "RTX 3070")
    service.index.rebuild_user(canonical_user_id="owner", persona_id="yogurt")
    service.memory.add_fact("owner", "gpu", "RTX 4080")

    result = service.recall(
        persona_id="yogurt", user_id="owner", surface="native",
        conversation_key="desktop", message="显卡是哪个型号？",
    )
    assert result["decision"] == "allow"
    assert result["memories"][0]["text"] == "gpu=RTX 4080"
    assert "3070" not in str(result["memories"])


def test_pre_gate_skips_memory_and_does_not_create_recall_audit(tmp_path):
    service = _service(tmp_path)
    service.memory.add_fact("owner", "gpu", "RTX 4080")
    result = service.recall(
        persona_id="yogurt", user_id="owner", surface="wechat",
        conversation_key="chat", message="好的",
    )
    assert result["decision"] == "skip"
    assert service.gate.events(user_id="owner") == []


def test_sqlite_compatibility_pragmas_and_fail_open_client(tmp_path):
    service = _service(tmp_path)
    with sqlite3.connect(service.db_path) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    assert MemoryServiceClient("http://127.0.0.1:1", timeout_seconds=0.05).recall({}) is None
