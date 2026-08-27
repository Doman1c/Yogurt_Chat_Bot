from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest

from yogurt_social.memory_benchmark import DATASET_VERSION, run_benchmark
from yogurt_social.memory_gate_experiment import (
    EXPERIMENT_V5B7_GATE_036,
    GateExperimentStore,
    evaluate_counterfactual,
    resolve_gate_experiment,
    run_fixed_corpus_guardrail,
    threshold_for,
)
from yogurt_social.memory_recall_gate import RecallCandidate
from yogurt_social.memory_service.app import create_app
from yogurt_social.memory_service.core import MemoryService, MemoryServiceConfig


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "social_layer" / "tests" / "fixtures"


def _candidate(score=0.5, kind="fact"):
    return RecallCandidate("stable", kind, 7, "gpu", "never export this text", score, score, 0.0, 1.0)


def _service(tmp_path, experiment=None):
    return MemoryService(MemoryServiceConfig(
        tmp_path / "memory.sqlite3", recall_gate_experiment=experiment,
    ))


def _seed(service):
    service.memory.add_fact("owner", "gpu", "RTX 4080")
    service.index.rebuild_user(canonical_user_id="owner", persona_id="yogurt")


def _recall(service, message, key="chat"):
    return service.recall(
        persona_id="yogurt", user_id="owner", surface="wechat",
        conversation_key=key, message=message,
    )


def _conversation_row(db_path):
    with sqlite3.connect(db_path) as connection:
        return connection.execute(
            """
            SELECT turn_index, last_allowed_turn, last_memory_kind,
                   last_memory_id, last_decision
            FROM memory_recall_conversations
            """
        ).fetchall()


def test_experiment_is_off_by_default_and_named_profile_is_fixed(tmp_path):
    service = _service(tmp_path)
    assert service.readiness()["gate_experiment"] == {
        "enabled": False, "name": "off", "candidate_base_min_score": None,
        "degraded": False,
    }
    profile = resolve_gate_experiment(EXPERIMENT_V5B7_GATE_036)
    assert profile is not None and profile.candidate_base_min_score == 0.36
    with pytest.raises(ValueError, match="invalid recall gate experiment"):
        resolve_gate_experiment("0.01")


def test_app_factory_reads_named_experiment_from_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("YOGURT_SOCIAL_DB", str(tmp_path / "memory.sqlite3"))
    monkeypatch.setenv("YOGURT_MEMORY_GATE_EXPERIMENT", EXPERIMENT_V5B7_GATE_036)
    app = create_app()
    assert app.state.memory_service.readiness()["gate_experiment"] == {
        "enabled": True, "name": EXPERIMENT_V5B7_GATE_036,
        "candidate_base_min_score": 0.36, "degraded": False,
    }


def test_threshold_formula_matches_production_adjustments_and_clamp():
    assert threshold_for(0.36, False, False) == 0.36
    assert threshold_for(0.36, True, False) == 0.24
    assert threshold_for(0.36, False, True) == pytest.approx(0.46)
    assert threshold_for(0.36, True, True) == pytest.approx(0.34)
    assert threshold_for(0.20, True, False) == 0.20
    assert threshold_for(0.95, False, True) == 0.95


def test_evaluator_preserves_safety_and_classifies_edge_cases():
    profile = resolve_gate_experiment(EXPERIMENT_V5B7_GATE_036)
    assert profile is not None
    recovered = evaluate_counterfactual(
        candidate=_candidate(0.4), baseline_decision="blocked", explicit_request=False,
        high_risk=False, production_reasons=["relevance_below_threshold"],
        candidate_base_min_score=profile.candidate_base_min_score,
    )
    assert recovered.candidate_decision == "allow"
    assert recovered.outcome == "recovered_by_candidate"
    protected = evaluate_counterfactual(
        candidate=_candidate(0.9), baseline_decision="blocked", explicit_request=False,
        high_risk=True, production_reasons=["high_risk_topic"],
        candidate_base_min_score=profile.candidate_base_min_score,
    )
    assert protected.candidate_decision == "blocked"
    pre_gate = evaluate_counterfactual(
        candidate=None, baseline_decision="skip", explicit_request=False, high_risk=False,
        production_reasons=[], candidate_base_min_score=0.36, pre_gate=True,
    )
    assert pre_gate.outcome == "not_evaluated_pre_gate"
    no_candidate = evaluate_counterfactual(
        candidate=None, baseline_decision="none", explicit_request=False, high_risk=False,
        production_reasons=[], candidate_base_min_score=0.36,
    )
    assert no_candidate.candidate_decision == "none"
    impossible = evaluate_counterfactual(
        candidate=_candidate(0.2), baseline_decision="allow", explicit_request=False,
        high_risk=False, production_reasons=[], candidate_base_min_score=0.36,
    )
    assert impossible.outcome == "inconsistent"


def test_runtime_response_and_cooldown_state_are_identical_off_and_on(tmp_path):
    off = _service(tmp_path / "off")
    on = _service(tmp_path / "on", EXPERIMENT_V5B7_GATE_036)
    _seed(off)
    _seed(on)
    assert _recall(off, "gpu RTX 4080", "same") == _recall(on, "gpu RTX 4080", "same")
    assert _recall(off, "gpu RTX 4080", "same") == _recall(on, "gpu RTX 4080", "same")
    assert _conversation_row(off.db_path) == _conversation_row(on.db_path)
    with sqlite3.connect(on.db_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM memory_gate_experiment_events").fetchone()[0] == 2


def test_experiment_writer_lock_contention_is_fail_open(tmp_path, monkeypatch):
    service = _service(tmp_path, EXPERIMENT_V5B7_GATE_036)
    _seed(service)
    assert service.gate_experiment_store is not None
    monkeypatch.setattr(
        service.gate_experiment_store, "record",
        lambda **_: (_ for _ in ()).throw(sqlite3.OperationalError("database is locked")),
    )
    result = _recall(service, "gpu RTX 4080")
    assert result["decision"] == "allow"
    assert service.readiness()["gate_experiment"]["degraded"] is True


def test_audit_store_uses_short_lock_budget(tmp_path):
    profile = resolve_gate_experiment(EXPERIMENT_V5B7_GATE_036)
    assert profile is not None
    store = GateExperimentStore(tmp_path / "memory.sqlite3")
    evaluation = evaluate_counterfactual(
        candidate=_candidate(0.4), baseline_decision="blocked", explicit_request=False,
        high_risk=False, production_reasons=["relevance_below_threshold"],
        candidate_base_min_score=0.36,
    )
    with sqlite3.connect(tmp_path / "memory.sqlite3") as lock:
        lock.execute("BEGIN EXCLUSIVE")
        started = time.monotonic()
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            store.record(
                profile=profile, baseline_base_threshold=0.52,
                baseline_decision="blocked", baseline_threshold=0.52,
                explicit_request=False, production_reasons=[],
                candidate=_candidate(0.4), evaluation=evaluation,
            )
        assert time.monotonic() - started < 0.5


def test_audit_retention_is_bounded_and_contains_no_identity_columns(tmp_path):
    profile = resolve_gate_experiment(EXPERIMENT_V5B7_GATE_036)
    assert profile is not None
    store = GateExperimentStore(tmp_path / "memory.sqlite3", max_events=2)
    evaluation = evaluate_counterfactual(
        candidate=_candidate(0.4), baseline_decision="blocked", explicit_request=False,
        high_risk=False, production_reasons=["relevance_below_threshold"],
        candidate_base_min_score=0.36,
    )
    for _ in range(3):
        store.record(
            profile=profile, baseline_base_threshold=0.52, baseline_decision="blocked",
            baseline_threshold=0.52, explicit_request=False,
            production_reasons=["relevance_below_threshold"], candidate=_candidate(0.4),
            evaluation=evaluation,
        )
    with sqlite3.connect(tmp_path / "memory.sqlite3") as connection:
        assert connection.execute("SELECT COUNT(*) FROM memory_gate_experiment_events").fetchone()[0] == 2
        columns = {row[1] for row in connection.execute("PRAGMA table_info(memory_gate_experiment_events)")}
    assert not {"user_id", "conversation_key", "message", "query", "text", "prompt"} & columns


def test_fixed_corpus_baseline_and_candidate_guardrail():
    benchmark = run_benchmark(
        FIXTURES / "memory_recall_seed.jsonl", FIXTURES / "memory_recall_cases.json",
    )
    guardrail = run_fixed_corpus_guardrail(
        FIXTURES / "memory_recall_seed.jsonl", FIXTURES / "memory_recall_cases.json",
    )
    assert guardrail["dataset_version"] == DATASET_VERSION
    assert benchmark["summary"]["false_recall_rate"] == 0.0
    assert guardrail["decision_parity"]["parity"] == 1.0
    assert guardrail["candidate_metrics"] == guardrail["b6_candidate_metrics"]
    assert guardrail["candidate_metrics"]["false_recall_rate"] == 0.0
    assert guardrail["candidate_metrics"]["stale_inactive_safety"] == 1.0
    assert guardrail["candidate_metrics"]["low_info_safety"] == 1.0
    assert guardrail["candidate_metrics"]["high_risk_suppression"] == 1.0
