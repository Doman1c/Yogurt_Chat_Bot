from __future__ import annotations

import json
from pathlib import Path

import pytest

from yogurt_social.memory_gate_analysis import (
    BASE_MIN_SCORE,
    BLOCKERS,
    _ablations,
    _coverage,
    _safe_frontier,
    _simulated_gate_decision,
    blocker_reasons,
    classify_positive,
    counterfactual_parity,
    margin_buckets,
    primary_blocker,
    run_analysis,
    threshold_for,
    validate_dataset_hashes,
)
from yogurt_social.memory_recall_gate import MemoryRecallGate


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "social_layer" / "tests" / "fixtures"


@pytest.fixture(scope="module")
def report():
    return run_analysis(
        FIXTURES / "memory_recall_seed.jsonl",
        FIXTURES / "memory_recall_cases.json",
    )


def _case(**overrides):
    item = {
        "case_id": "unit", "category": "exact", "tags": ["fact"],
        "should_recall": True, "pre_gate": False, "score": 0.45,
        "explicit_request": False, "high_risk": False,
        "production_blockers": [], "real_gate_decision": "blocked",
        "top_candidate_expected": True,
    }
    item.update(overrides)
    return item


def test_positive_partition_is_mutually_exclusive_and_exhaustive(report):
    partition = report["positive_partition"]
    assert sum(partition.values()) == report["positive_case_count"]
    assert set(partition) == {
        "pre_gate_positive_skip", "retrieval_miss", "ranking_miss",
        "gate_blocked_expected_top", "success_allow_expected", "inconsistent_return",
    }


def test_retrieval_and_ranking_misses_are_distinct():
    assert classify_positive(
        pre_gate=False, expected_memory_ids=["expected"], ranked_memory_ids=[],
        gate_decision="none", returned_memory_id=None,
    ) == "retrieval_miss"
    assert classify_positive(
        pre_gate=False, expected_memory_ids=["expected"], ranked_memory_ids=["other", "expected"],
        gate_decision="blocked", returned_memory_id=None,
    ) == "ranking_miss"


def test_gate_blocked_expected_top_requires_expected_top_candidate():
    assert classify_positive(
        pre_gate=False, expected_memory_ids=["expected"], ranked_memory_ids=["expected"],
        gate_decision="blocked", returned_memory_id=None,
    ) == "gate_blocked_expected_top"
    assert classify_positive(
        pre_gate=False, expected_memory_ids=["expected"], ranked_memory_ids=["other", "expected"],
        gate_decision="blocked", returned_memory_id=None,
    ) != "gate_blocked_expected_top"


def test_blocker_decomposition_is_multilabel_and_primary_is_deterministic():
    reasons = [
        "relevance_below_threshold", "emotion_non_interruption",
        "candidate_kind:fact", "gate_block",
    ]
    assert blocker_reasons(reasons) == ["relevance_below_threshold", "emotion_non_interruption"]
    assert primary_blocker(reasons) == "relevance_below_threshold"


def test_score_margin_and_buckets_are_correct():
    buckets = margin_buckets([
        {"score_margin": -0.21}, {"score_margin": -0.10},
        {"score_margin": -0.05}, {"score_margin": -0.01},
        {"score_margin": 0.00}, {"score_margin": 0.05},
    ])
    assert buckets == {
        "< -0.20": 1, "[-0.20, -0.10)": 0, "[-0.10, -0.05)": 1,
        "[-0.05, 0)": 2, "[0, 0.05)": 1, ">= 0.05": 1,
    }


def test_threshold_formula_matches_gate_rules_and_clamp():
    assert threshold_for(0.48, False, False) == 0.48
    assert threshold_for(0.48, True, False) == 0.36
    assert threshold_for(0.48, False, True) == 0.58
    assert threshold_for(0.48, True, True) == pytest.approx(0.46)
    assert threshold_for(0.10, True, False) == 0.20
    assert threshold_for(0.95, False, True) == 0.95


def test_counterfactual_changes_only_relevance_threshold():
    protected = _case(
        score=0.9, production_blockers=["high_risk_topic"], high_risk=True,
    )
    assert _simulated_gate_decision(protected, base_min_score=0.30) == "blocked"
    relevance = _case(
        score=0.40, production_blockers=["relevance_below_threshold"],
    )
    assert _simulated_gate_decision(relevance, base_min_score=0.48) == "blocked"
    assert _simulated_gate_decision(relevance, base_min_score=0.30) == "allow"


def test_baseline_parity_is_complete(report):
    parity = report["baseline_parity"]
    assert parity["parity"] == 1.0
    assert parity["matching_cases"] == parity["checked_cases"]
    assert counterfactual_parity(report["case_diagnostics"])["parity"] == 1.0


def test_zero_false_recall_frontier_prefers_conservative_tie():
    frontier = _safe_frontier([
        {"base_min_score": 0.48, "correct_positive_recall_count": 2, "correct_positive_recall_rate": 0.2, "false_recall_rate": 0.0, "stale_inactive_safety": 1.0, "low_info_safety": 1.0, "high_risk_suppression": 1.0},
        {"base_min_score": 0.40, "correct_positive_recall_count": 3, "correct_positive_recall_rate": 0.3, "false_recall_rate": 0.0, "stale_inactive_safety": 1.0, "low_info_safety": 1.0, "high_risk_suppression": 1.0},
        {"base_min_score": 0.41, "correct_positive_recall_count": 3, "correct_positive_recall_rate": 0.3, "false_recall_rate": 0.0, "stale_inactive_safety": 1.0, "low_info_safety": 1.0, "high_risk_suppression": 1.0},
    ])
    assert frontier["best_zero_false_recall_candidate"]["base_min_score"] == 0.41
    assert frontier["additional_allows"] == 1


def test_ablation_and_coverage_keep_unexercised_rules_visible(report):
    ablation = _ablations(report["case_diagnostics"])
    assert set(ablation) == set(BLOCKERS)
    assert "new_false_recall_count" in ablation["relevance_below_threshold"]
    coverage = _coverage(report["case_diagnostics"])
    assert coverage["conversation_cooldown"]["exercised_cases"] == 0
    assert "insufficient coverage" in coverage["conversation_cooldown"]["coverage"]


def test_default_output_is_metadata_only(report):
    serialized = json.dumps(report, ensure_ascii=False)
    assert "我的 GPU 是什么" not in serialized
    assert "RTX 4080" not in serialized
    assert "benchmark-owner" not in serialized
    assert "gate-analysis-" not in serialized
    assert report["privacy"] == {
        "query_text_exported": False, "raw_memory_text_exported": False,
        "identity_exported": False, "conversation_key_exported": False,
    }


def test_dataset_hash_mismatch_fails_explicitly(tmp_path):
    altered = tmp_path / "seed.jsonl"
    altered.write_text((FIXTURES / "memory_recall_seed.jsonl").read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="analysis dataset hash mismatch"):
        validate_dataset_hashes(altered, FIXTURES / "memory_recall_cases.json")


def test_analysis_does_not_change_production_gate_configuration(tmp_path, report):
    before = MemoryRecallGate(tmp_path / "before.sqlite3")
    after = MemoryRecallGate(tmp_path / "after.sqlite3")
    assert before.min_score == BASE_MIN_SCORE
    assert after.min_score == BASE_MIN_SCORE
    assert report["configuration"]["production_gate_changed"] is False
