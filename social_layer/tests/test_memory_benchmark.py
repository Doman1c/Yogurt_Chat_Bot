import json
import copy
from pathlib import Path

import pytest

from yogurt_social.memory_benchmark import (
    DEFAULT_USER_ID,
    _metrics,
    load_cases,
    load_seed,
    run_benchmark,
    seed_service,
    validate_baseline_dataset,
)
from yogurt_social.memory_service.core import MemoryService, MemoryServiceConfig


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "social_layer" / "tests" / "fixtures"


def test_committed_corpus_is_versioned_sanitized_and_broad():
    seed = load_seed(FIXTURES / "memory_recall_seed.jsonl")
    cases = load_cases(FIXTURES / "memory_recall_cases.json")
    assert len(seed) == 12
    assert 100 <= len(cases) <= 140
    assert {"exact", "paraphrase", "cross_language", "hard_negative", "stale", "emotion", "high_risk", "low_info"} <= {case["category"] for case in cases}
    assert all("password" not in json.dumps(item, ensure_ascii=False).casefold() for item in seed)


def test_seed_rejects_sensitive_canonical_text(tmp_path):
    path = tmp_path / "seed.jsonl"
    path.write_text('{"id":"bad","type":"fact","key":"password","value":"not-safe","active":true}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="sensitive"):
        load_seed(path)


def test_metrics_cover_rank_false_recall_and_no_recall_precision():
    report = _metrics([
        {"should_recall": True, "top1_correct": True, "recall_at_3": True, "reciprocal_rank": 1.0, "decision": "allow", "decision_correct": True},
        {"should_recall": True, "top1_correct": False, "recall_at_3": True, "reciprocal_rank": 0.5, "decision": "blocked", "decision_correct": False},
        {"should_recall": False, "top1_correct": False, "recall_at_3": False, "reciprocal_rank": 0.0, "decision": "allow", "decision_correct": False},
        {"should_recall": False, "top1_correct": False, "recall_at_3": False, "reciprocal_rank": 0.0, "decision": "none", "decision_correct": True},
    ])
    assert report["top1_accuracy"] == 0.5
    assert report["recall_at_3"] == 1.0
    assert report["mrr"] == 0.75
    assert report["false_recall_rate"] == 0.5
    assert report["no_recall_precision"] == 0.5


@pytest.mark.parametrize("field", ["dataset_version", "seed_sha256", "cases_sha256"])
def test_dataset_mismatch_blocks_regression_comparison(field):
    report = {
        "dataset_version": "memory-recall-v1",
        "dataset": {"seed_sha256": "seed-a", "cases_sha256": "cases-a"},
        "summary": {"false_recall_rate": 0.0},
        "safety": {},
    }
    baseline = copy.deepcopy(report)
    baseline["thresholds"] = {}
    if field == "dataset_version":
        baseline[field] = "memory-recall-v2"
    else:
        baseline["dataset"][field] = "different"
    with pytest.raises(ValueError, match="benchmark dataset mismatch"):
        validate_baseline_dataset(report, baseline)


def test_benchmark_uses_real_service_and_keeps_surface_state_isolated(tmp_path):
    service = MemoryService(MemoryServiceConfig(tmp_path / "memory.sqlite3"))
    source_map = seed_service(service, load_seed(FIXTURES / "memory_recall_seed.jsonl"))
    wechat = service.recall(persona_id="yogurt", user_id=DEFAULT_USER_ID, surface="wechat", conversation_key="same", message="TTS 80:20")
    native = service.recall(persona_id="yogurt", user_id=DEFAULT_USER_ID, surface="native", conversation_key="same", message="TTS 80:20")
    assert wechat["decision"] == "allow"
    assert native["decision"] == "allow"
    assert service.states.get(DEFAULT_USER_ID, "wechat:same") is not None
    assert service.states.get(DEFAULT_USER_ID, "native:same") is not None
    assert source_map[("episode", wechat["memories"][0]["source_id"])] == "episode_tts_8020"


def test_mini_benchmark_runs_real_pipeline_and_classifies_negative(tmp_path):
    seed = FIXTURES / "memory_recall_seed.jsonl"
    cases = tmp_path / "cases.json"
    cases.write_text(json.dumps({"schema_version": 1, "cases": [
        {"id": "positive", "query": "TTS 80:20", "should_recall": True, "expected_memory_ids": ["episode_tts_8020"], "acceptable_memory_ids": [], "surface": "wechat", "category": "exact", "difficulty": "easy", "tags": [], "notes": ""},
        {"id": "negative", "query": "RTX 5090 功耗", "should_recall": False, "expected_memory_ids": [], "acceptable_memory_ids": [], "surface": "native", "category": "hard_negative", "difficulty": "hard", "tags": [], "notes": ""},
    ]}, ensure_ascii=False), encoding="utf-8")
    report = run_benchmark(seed, cases)
    assert report["summary"]["cases"] == 2
    assert report["categories"]["hard_negative"]["false_recalls"] == 0
    assert report["results"][0]["ranked_memory_ids"] == ["episode_tts_8020"]
