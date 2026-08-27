import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from yogurt_social.memory_recall_gate import RecallCandidate, RecallDecision
from yogurt_social.memory_service.app import create_app
from yogurt_social.memory_service.client import MemoryServiceClient
from yogurt_social.memory_service.core import MemoryService, MemoryServiceConfig


def _service(tmp_path: Path) -> MemoryService:
    return MemoryService(MemoryServiceConfig(tmp_path / "memory.sqlite3"))


def _seed_gpu(service: MemoryService, value: str = "RTX 4080") -> None:
    service.memory.add_fact("owner", "gpu", value)
    service.index.rebuild_user(canonical_user_id="owner", persona_id="yogurt")


def _recall(service: MemoryService, message: str, *, surface: str = "wechat", key: str = "chat", **extra):
    return service.recall(
        persona_id="yogurt", user_id="owner", surface=surface,
        conversation_key=key, message=message, **extra,
    )


def test_shared_long_term_memory_and_isolated_surface_state(tmp_path):
    service = _service(tmp_path)
    service.observe(
        persona_id="yogurt", user_id="owner", surface="wechat",
        conversation_key="friend-a", phase="user", user_message="我的显卡是 RTX 4080",
        idempotency_key="turn-user-1",
    )
    written = service.observe(
        persona_id="yogurt", user_id="owner", surface="wechat",
        conversation_key="friend-a", phase="assistant", user_message="我的显卡是 RTX 4080",
        assistant_reply="收到。", idempotency_key="turn-assistant-1",
    )
    assert written["promoted_count"] == 1
    result = _recall(service, "gpu RTX 4080", surface="native", key="desktop")
    assert result["decision"] == "allow"
    assert result["memories"][0]["text"] == "gpu=RTX 4080"
    assert service.states.get("owner", "wechat:friend-a") is not None
    assert service.states.get("owner", "native:desktop") is not None
    assert service.states.get("owner", "wechat:desktop") is None


def test_recall_observes_each_current_turn_and_respects_emotion(tmp_path):
    service = _service(tmp_path)
    _seed_gpu(service)
    assert _recall(service, "gpu RTX 4080", key="emotion")["decision"] == "allow"
    sad = _recall(service, "我很难过，gpu RTX 4080", key="emotion")
    assert sad["decision"] == "blocked"
    assert "emotion_non_interruption" in sad["reason"]

    service = _service(tmp_path / "fresh")
    _seed_gpu(service)
    sad = _recall(service, "我很难过，gpu RTX 4080", key="current-turn")
    assert "emotion_non_interruption" in sad["reason"]
    neutral = _recall(service, "gpu RTX 4080 技术问题", key="current-turn")
    assert "emotion_non_interruption" not in neutral["reason"]
    assert service.states.get("owner", "wechat:current-turn").user_mood == "neutral"


def test_low_information_high_risk_and_unrelated_queries_do_not_recall(tmp_path):
    service = _service(tmp_path)
    _seed_gpu(service)
    assert _recall(service, "好的")["decision"] == "skip"
    high_risk = _recall(service, "我的签证和 gpu RTX 4080 有关系吗？", key="risk")
    assert high_risk["decision"] == "blocked"
    assert "high_risk_topic" in high_risk["reason"]
    unrelated = _recall(service, "晚饭吃什么？", key="unrelated")
    assert unrelated["memories"] == []


def test_fts_stale_tokens_are_deleted_on_rebuild(tmp_path):
    service = _service(tmp_path)
    _seed_gpu(service, "RTX 3070")
    assert service.index.search(canonical_user_id="owner", message="RTX 3070")
    service.memory.add_fact("owner", "gpu", "RTX 4080")
    service.index.rebuild_user(canonical_user_id="owner", persona_id="yogurt")
    with sqlite3.connect(service.db_path) as connection:
        stale_fts = connection.execute(
            """
            SELECT d.prompt_text FROM memory_search_fts f
            JOIN memory_search_documents d ON d.rowid = f.rowid
            WHERE f.search_text MATCH ?
            """,
            ("3070",),
        ).fetchall()
    assert stale_fts == []
    fresh = service.index.search(canonical_user_id="owner", message="RTX 4080")
    assert [item.prompt_text for item in fresh] == ["gpu=RTX 4080"]


def test_observe_idempotency_and_index_failure_do_not_duplicate_canonical_write(tmp_path, monkeypatch):
    service = _service(tmp_path)
    monkeypatch.setattr(service.index, "rebuild_user", lambda **_: (_ for _ in ()).throw(RuntimeError("fts down")))
    first = service.observe(
        persona_id="yogurt", user_id="owner", surface="wechat", conversation_key="chat",
        phase="assistant", user_message="我的显卡是 RTX 4080", assistant_reply="收到",
        idempotency_key="same-turn",
    )
    second = service.observe(
        persona_id="yogurt", user_id="owner", surface="wechat", conversation_key="chat",
        phase="assistant", user_message="我的显卡是 RTX 4080", assistant_reply="收到",
        idempotency_key="same-turn",
    )
    assert first["canonical_write"] is True
    assert first["index_updated"] is False and first["index_dirty"] is True
    assert second["idempotent"] is True
    with sqlite3.connect(service.db_path) as connection:
        assert connection.execute("SELECT occurrences FROM memory_candidates").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM memory_revisions").fetchone()[0] == 1


def test_in_progress_idempotency_record_prevents_crash_retry_replay(tmp_path, monkeypatch):
    service = _service(tmp_path)
    original_complete = service._complete_idempotent
    monkeypatch.setattr(service, "_complete_idempotent", lambda *args: (_ for _ in ()).throw(RuntimeError("crash window")))
    with pytest.raises(RuntimeError, match="crash window"):
        service.observe(
            persona_id="yogurt", user_id="owner", surface="wechat", conversation_key="chat",
            phase="assistant", user_message="我的显卡是 RTX 4080", assistant_reply="收到",
            idempotency_key="crash-safe-turn",
        )
    monkeypatch.setattr(service, "_complete_idempotent", original_complete)
    retry = service.observe(
        persona_id="yogurt", user_id="owner", surface="wechat", conversation_key="chat",
        phase="assistant", user_message="我的显卡是 RTX 4080", assistant_reply="收到",
        idempotency_key="crash-safe-turn",
    )
    assert retry["in_progress"] is True
    with sqlite3.connect(service.db_path) as connection:
        assert connection.execute("SELECT occurrences FROM memory_candidates").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM memory_revisions").fetchone()[0] == 1


def test_gate_candidate_and_token_budget_cannot_substitute_memory(tmp_path, monkeypatch):
    service = _service(tmp_path)
    allowed = RecallCandidate("stable", "fact", 1, "a", "this candidate is deliberately too long", 0.9, 0.9, 0.0, 1.0)
    monkeypatch.setattr(
        service.gate,
        "evaluate",
        lambda **_: RecallDecision("shadow_only", "allow", 0.52, False, 1, 1, 0, 0, ("gate_allow",), allowed),
    )
    result = _recall(service, "anything useful", max_tokens=2)
    assert result["decision"] == "blocked"
    assert result["memories"] == []
    assert result["token_estimate"] == 0
    assert "token_budget_exceeded" in result["reason"]


def test_client_loopback_and_fail_open(tmp_path):
    _service(tmp_path)
    assert MemoryServiceClient("http://127.0.0.1:1", timeout_seconds=0.05).recall({}) is None
    try:
        MemoryServiceClient("http://192.168.1.2:17621")
    except ValueError:
        pass
    else:
        raise AssertionError("LAN client endpoint must be rejected")


def test_fastapi_http_smoke_and_idempotent_observe(tmp_path):
    service = _service(tmp_path)
    client = TestClient(create_app(service))
    assert client.get("/v1/health").json()["ok"] is True
    assert client.get("/v1/ready").json()["db"] == "ready"
    payload = {
        "persona_id": "yogurt", "user_id": "owner", "surface": "wechat",
        "conversation_key": "http", "phase": "assistant",
        "user_message": "我的显卡是 RTX 4080", "assistant_reply": "收到",
        "idempotency_key": "http-turn",
    }
    first = client.post("/v1/memory/observe", json=payload)
    second = client.post("/v1/memory/observe", json=payload)
    assert first.status_code == second.status_code == 200
    assert second.json()["idempotent"] is True
    recall = client.post("/v1/memory/recall", json={
        "persona_id": "yogurt", "user_id": "owner", "surface": "wechat",
        "conversation_key": "http", "message": "gpu RTX 4080",
    })
    assert recall.status_code == 200
    assert client.get("/v1/memory/snapshot").status_code == 200


def test_shadow_only_openclaw_configuration():
    repo = Path(__file__).resolve().parents[2]
    manifest = (repo / "integrations/openclaw/openclaw.plugin.json").read_text(encoding="utf-8")
    runtime = (repo / "integrations/openclaw/index-v4.ts").read_text(encoding="utf-8")
    assert '"memoryServiceMode"' not in manifest
    assert "[Yogurt Relevant Memory]" not in runtime
