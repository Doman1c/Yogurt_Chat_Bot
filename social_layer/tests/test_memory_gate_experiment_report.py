from __future__ import annotations

import json

from yogurt_social.memory_gate_experiment import (
    EXPERIMENT_V5B7_GATE_036,
    GateExperimentStore,
    evaluate_counterfactual,
    resolve_gate_experiment,
)
from yogurt_social.memory_gate_experiment_report import markdown_report, summarize_experiment
from yogurt_social.memory_recall_gate import RecallCandidate


def _candidate():
    return RecallCandidate(
        "stable", "fact", 1, "marker", "RAW-MEMORY-TEXT-MUST-NOT-EXPORT",
        0.4, 0.4, 0.0, 1.0,
    )


def test_report_is_aggregate_only_and_privacy_safe(tmp_path):
    profile = resolve_gate_experiment(EXPERIMENT_V5B7_GATE_036)
    assert profile is not None
    store = GateExperimentStore(tmp_path / "memory.sqlite3")
    evaluation = evaluate_counterfactual(
        candidate=_candidate(), baseline_decision="blocked", explicit_request=False,
        high_risk=False, production_reasons=["relevance_below_threshold"],
        candidate_base_min_score=0.36,
    )
    store.record(
        profile=profile, baseline_base_threshold=0.52, baseline_decision="blocked",
        baseline_threshold=0.52, explicit_request=False,
        production_reasons=["relevance_below_threshold"], candidate=_candidate(),
        evaluation=evaluation,
    )
    summary = summarize_experiment(tmp_path / "memory.sqlite3", EXPERIMENT_V5B7_GATE_036)
    assert summary["total_evaluated_events"] == 1
    assert summary["outcomes"]["recovered_by_candidate"] == 1
    assert summary["candidate_kind"] == {"fact": 1}
    assert summary["blocker_distribution"] == {"relevance_below_threshold": 1}
    assert summary["candidate_recovery"] == {"recovered_count": 1, "recovered_rate": 1.0}
    serialized = json.dumps(summary, ensure_ascii=False) + markdown_report(summary)
    assert "RAW-MEMORY-TEXT-MUST-NOT-EXPORT" not in serialized
    assert "user_id" not in serialized
    assert summary["interpretation"] == (
        "Real shadow traffic is unlabeled. Candidate recovery is not equivalent to correctness."
    )


def test_report_handles_absent_experiment_table(tmp_path):
    summary = summarize_experiment(tmp_path / "empty.sqlite3", EXPERIMENT_V5B7_GATE_036)
    assert summary["total_evaluated_events"] == 0
    assert summary["baseline"] == {
        "allow": 0, "blocked": 0, "none": 0, "pre_gate_skipped": 0,
    }
