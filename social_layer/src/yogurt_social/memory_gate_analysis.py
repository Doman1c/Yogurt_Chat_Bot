"""Privacy-safe, offline calibration analysis for the V5 Recall Gate.

The module deliberately does not alter :mod:`memory_recall_gate`.  It creates a
fresh SQLite database, seeds the committed benchmark corpus, and uses the real
``MemoryService`` / FTS index / Recall Gate.  JSON and Markdown exports contain
only fixture case IDs and opaque fixture memory IDs; they never export queries,
memory text, identities, or conversation keys.
"""

from __future__ import annotations

import argparse
import json
import math
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .memory_benchmark import (
    DATASET_VERSION,
    DEFAULT_USER_ID,
    _expected_ids,
    _sha256,
    load_cases,
    load_seed,
    seed_service,
)
from .memory_recall_gate import RecallCandidate
from .memory_service.core import MemoryService, MemoryServiceConfig


ANALYSIS_SCHEMA_VERSION = 1
BASE_MIN_SCORE = 0.48
SWEEP_START = 0.30
SWEEP_END = 0.55
SWEEP_STEP = 0.01
EXPECTED_SEED_SHA256 = "4e3a3d9ec9b0f30ea9c353bfa6d43d99b50a2cd159164ad4e51a3e1ec4964166"
EXPECTED_CASES_SHA256 = "d09709f4c626f1653146dbb084ec385f9d4af1308cb6b8b93c41229712020e1c"
BLOCKERS = (
    "relevance_below_threshold",
    "low_information_turn",
    "emotion_non_interruption",
    "moment_non_interruption",
    "high_risk_topic",
    "conversation_cooldown",
    "same_memory_cooldown",
    "low_memory_quality",
)
_META_REASONS = {
    "explicit_recall_request", "gate_allow", "gate_block", "gate_noop",
}
_DISTRIBUTION_FIELDS = (
    "score", "threshold", "score_margin", "lexical_score",
    "continuity_score", "quality_score",
)


def threshold_for(base_min_score: float, explicit_request: bool, high_risk: bool) -> float:
    """Mirror the production threshold formula without mutating a Gate."""
    threshold = float(base_min_score) - (0.12 if explicit_request else 0.0)
    if high_risk:
        threshold += 0.10
    return min(0.95, max(0.20, threshold))


def validate_dataset_hashes(seed_path: Path, cases_path: Path) -> None:
    """Fail closed for the fixed V5-B.5 corpus used by this calibration."""
    seed_hash = _sha256(seed_path)
    cases_hash = _sha256(cases_path)
    mismatches = []
    if seed_hash != EXPECTED_SEED_SHA256:
        mismatches.append("seed_sha256=%s" % seed_hash)
    if cases_hash != EXPECTED_CASES_SHA256:
        mismatches.append("cases_sha256=%s" % cases_hash)
    if mismatches:
        raise ValueError("analysis dataset hash mismatch: " + "; ".join(mismatches))


def blocker_reasons(reasons: Iterable[str]) -> List[str]:
    """Return real blockers, preserving the production reason order."""
    return [reason for reason in reasons if reason in BLOCKERS]


def primary_blocker(reasons: Iterable[str]) -> Optional[str]:
    blockers = blocker_reasons(reasons)
    return blockers[0] if blockers else None


def classify_positive(
    *,
    pre_gate: bool,
    expected_memory_ids: Iterable[str],
    ranked_memory_ids: Sequence[str],
    gate_decision: str,
    returned_memory_id: Optional[str],
) -> str:
    """Classify a positive exactly once, in the B.6 required order."""
    expected = set(expected_memory_ids)
    if pre_gate:
        return "pre_gate_positive_skip"
    if not expected.intersection(ranked_memory_ids):
        return "retrieval_miss"
    top = ranked_memory_ids[0] if ranked_memory_ids else None
    if top not in expected:
        return "ranking_miss"
    if gate_decision != "allow":
        return "gate_blocked_expected_top"
    if returned_memory_id in expected:
        return "success_allow_expected"
    return "inconsistent_return"


def _ranked_candidates(
    service: MemoryService,
    *,
    query: str,
    surface: str,
    conversation_key: str,
) -> List[RecallCandidate]:
    """Recreate the small candidate assembly in ``MemoryService.recall``.

    The service has already observed the user turn at this point, so this uses
    the same state, Exact+FTS top-8 set, score function, and sort keys as the
    production call that produced the stored Gate event.
    """
    scoped_key = service.scoped_conversation_key(surface, conversation_key)
    state = service.states.get(DEFAULT_USER_ID, scoped_key)
    if state is None:
        return []
    explicit = service.gate._explicit_request(query)
    continuity = [state.current_topic or "", *state.open_threads]
    candidates: List[RecallCandidate] = []
    for document in service.index.search(
        canonical_user_id=DEFAULT_USER_ID, message=query, limit=8,
    ):
        score, lexical, continuity_score, quality = service.gate._score(
            message=query,
            continuity_texts=continuity,
            candidate_text=document.search_text,
            quality=document.quality,
            explicit_request=explicit,
        )
        candidates.append(RecallCandidate(
            "episodic" if document.source_kind == "episode" else "stable",
            document.source_kind,
            document.source_id,
            document.memory_key,
            document.prompt_text,
            score,
            lexical,
            continuity_score,
            quality,
        ))
    candidates.sort(
        key=lambda item: (item.score, item.lexical_score, item.quality_score),
        reverse=True,
    )
    return candidates


def _fixture_id(
    candidate: Optional[RecallCandidate], source_map: Dict[Tuple[str, int], str],
) -> Optional[str]:
    if candidate is None:
        return None
    return source_map.get((candidate.kind, candidate.source_id))


def _candidate_diagnostics(
    candidate: Optional[RecallCandidate],
    *,
    threshold: Optional[float],
    source_map: Dict[Tuple[str, int], str],
) -> Dict[str, Any]:
    """Metadata-only top-candidate diagnostics; no candidate text is retained."""
    if candidate is None:
        return {
            "top_candidate_id": None, "candidate_layer": None,
            "candidate_kind": None, "score": None, "threshold": threshold,
            "score_margin": None, "lexical_score": None,
            "continuity_score": None, "quality_score": None,
        }
    return {
        "top_candidate_id": _fixture_id(candidate, source_map),
        "candidate_layer": candidate.layer,
        "candidate_kind": candidate.kind,
        "score": round(candidate.score, 6),
        "threshold": round(threshold, 6) if threshold is not None else None,
        "score_margin": round(candidate.score - threshold, 6) if threshold is not None else None,
        "lexical_score": round(candidate.lexical_score, 6),
        "continuity_score": round(candidate.continuity_score, 6),
        "quality_score": round(candidate.quality_score, 6),
    }


def _run_cases(seed_path: Path, cases_path: Path) -> List[Dict[str, Any]]:
    records = load_seed(seed_path)
    cases = load_cases(cases_path)
    results: List[Dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="yogurt-memory-gate-analysis-") as directory:
        # This is an isolated analytical service configuration.  It does not
        # write a production configuration or change MemoryRecallGate defaults.
        service = MemoryService(MemoryServiceConfig(
            Path(directory) / "memory.sqlite3", recall_min_score=BASE_MIN_SCORE,
        ))
        source_map = seed_service(service, records)
        for case in cases:
            conversation_key = "gate-analysis-" + str(case["id"])
            response = service.recall(
                persona_id="yogurt",
                user_id=DEFAULT_USER_ID,
                surface=case["surface"],
                conversation_key=conversation_key,
                message=case["query"],
            )
            pre_gate = response.get("decision") == "skip"
            ranked = [] if pre_gate else _ranked_candidates(
                service,
                query=case["query"],
                surface=case["surface"],
                conversation_key=conversation_key,
            )
            ranked_ids = [
                fixture_id for fixture_id in
                (_fixture_id(candidate, source_map) for candidate in ranked)
                if fixture_id is not None
            ]
            scoped_key = service.scoped_conversation_key(case["surface"], conversation_key)
            events = service.gate.events(
                user_id=DEFAULT_USER_ID, conversation_key=scoped_key, limit=1,
            )
            event = events[0] if events else None
            gate_decision = "skip" if pre_gate else (str(event["decision"]) if event else "none")
            reasons = ([] if pre_gate else list(event.get("reasons", []))) if event else list(response.get("reason", []))
            returned_id = None
            memories = response.get("memories", [])
            if memories:
                first = memories[0]
                returned_id = source_map.get((str(first["kind"]), int(first["source_id"])))
            threshold = float(event["threshold"]) if event else None
            candidate = ranked[0] if ranked else None
            expected = sorted(_expected_ids(case))
            best_rank = next(
                (number for number, memory_id in enumerate(ranked_ids, start=1)
                 if memory_id in set(expected)),
                None,
            )
            diagnostics = _candidate_diagnostics(
                candidate, threshold=threshold, source_map=source_map,
            )
            result: Dict[str, Any] = {
                "case_id": case["id"],
                "category": case["category"],
                "tags": list(case.get("tags", [])),
                "surface": case["surface"],
                "should_recall": bool(case["should_recall"]),
                "expected_memory_ids": expected,
                "expected_best_rank": best_rank,
                "candidate_count": len(ranked_ids),
                "top_8_candidate_ids": ranked_ids[:8],
                "top_candidate_expected": bool(ranked_ids and ranked_ids[0] in set(expected)),
                "returned_memory_id": returned_id,
                "pre_gate": pre_gate,
                "real_service_decision": str(response.get("decision")),
                "real_gate_decision": gate_decision,
                "real_reasons": reasons,
                "explicit_request": bool(event["explicit_request"]) if event else False,
                "high_risk": service.gate._high_risk_topic(case["query"]),
                "production_blockers": blocker_reasons(reasons),
            }
            result.update(diagnostics)
            if result["should_recall"]:
                result["positive_partition"] = classify_positive(
                    pre_gate=pre_gate,
                    expected_memory_ids=expected,
                    ranked_memory_ids=ranked_ids,
                    gate_decision=gate_decision,
                    returned_memory_id=returned_id,
                )
            else:
                result["negative_outcome"] = (
                    "false_recall" if response.get("decision") == "allow" else "correct_no_recall"
                )
            results.append(result)
    return results


def _percent(numerator: int, denominator: int) -> float:
    return round(float(numerator) / denominator, 4) if denominator else 0.0


def _percentile(values: Sequence[float], percentile: int) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    index = max(0, math.ceil((percentile / 100.0) * len(ordered)) - 1)
    return round(ordered[index], 6)


def distribution_summary(values: Sequence[float]) -> Dict[str, Optional[float]]:
    return {
        "min": _percentile(values, 0), "p10": _percentile(values, 10),
        "p25": _percentile(values, 25), "p50": _percentile(values, 50),
        "p75": _percentile(values, 75), "p90": _percentile(values, 90),
        "max": _percentile(values, 100),
    }


def _distribution_groups(results: Sequence[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    top_expected = [
        item for item in results
        if item["should_recall"] and item["top_candidate_expected"]
    ]
    return {
        "all_top_candidate_expected_positives": top_expected,
        "success_allow_expected": [
            item for item in top_expected
            if item.get("positive_partition") == "success_allow_expected"
        ],
        "gate_blocked_expected_top": [
            item for item in top_expected
            if item.get("positive_partition") == "gate_blocked_expected_top"
        ],
        "explicit_recall_positives": [
            item for item in top_expected if item["explicit_request"]
        ],
        "non_explicit_positives": [
            item for item in top_expected if not item["explicit_request"]
        ],
        "fact": [item for item in top_expected if "fact" in item["tags"]],
        "preference": [item for item in top_expected if "preference" in item["tags"]],
        "episode": [item for item in top_expected if "episode" in item["tags"]],
    }


def _score_distributions(results: Sequence[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    summary: Dict[str, Dict[str, Any]] = {}
    for name, items in _distribution_groups(results).items():
        values: Dict[str, Any] = {"count": len(items)}
        for field in _DISTRIBUTION_FIELDS:
            values[field] = distribution_summary([
                float(item[field]) for item in items if item.get(field) is not None
            ])
        summary[name] = values
    return summary


def margin_buckets(results: Sequence[Dict[str, Any]]) -> Dict[str, int]:
    labels = (
        "< -0.20", "[-0.20, -0.10)", "[-0.10, -0.05)",
        "[-0.05, 0)", "[0, 0.05)", ">= 0.05",
    )
    counts = Counter({label: 0 for label in labels})
    for item in results:
        margin = item.get("score_margin")
        if margin is None:
            continue
        if margin < -0.20:
            label = "< -0.20"
        elif margin < -0.10:
            label = "[-0.20, -0.10)"
        elif margin < -0.05:
            label = "[-0.10, -0.05)"
        elif margin < 0:
            label = "[-0.05, 0)"
        elif margin < 0.05:
            label = "[0, 0.05)"
        else:
            label = ">= 0.05"
        counts[label] += 1
    return dict(counts)


def _simulated_gate_decision(
    item: Dict[str, Any],
    *,
    base_min_score: float,
    ignored_blocker: Optional[str] = None,
) -> str:
    """Counterfactual Gate decision, varying only relevance threshold.

    Non-relevance blockers are copied from the real Gate event.  This makes a
    sweep incapable of silently relaxing high-risk, cooldown, mood, moment,
    low-information, or quality protection.
    """
    if item["pre_gate"]:
        return "skip"
    if item.get("score") is None:
        return "none"
    threshold = threshold_for(
        base_min_score, bool(item["explicit_request"]), bool(item["high_risk"]),
    )
    # Recompute relevance from the counterfactual threshold.  Every other
    # blocker is intentionally copied from the real event unchanged.
    blockers = [
        reason for reason in item["production_blockers"]
        if reason not in {"relevance_below_threshold", ignored_blocker}
    ]
    if ignored_blocker != "relevance_below_threshold" and float(item["score"]) < threshold:
        blockers.append("relevance_below_threshold")
    return "blocked" if blockers else "allow"


def _simulation_metrics(
    results: Sequence[Dict[str, Any]],
    *,
    base_min_score: float,
    ignored_blocker: Optional[str] = None,
) -> Dict[str, Any]:
    simulated = [
        _simulated_gate_decision(
            item, base_min_score=base_min_score, ignored_blocker=ignored_blocker,
        ) for item in results
    ]
    positives = [
        (item, decision) for item, decision in zip(results, simulated)
        if item["should_recall"]
    ]
    negatives = [
        (item, decision) for item, decision in zip(results, simulated)
        if not item["should_recall"]
    ]
    allows = sum(decision == "allow" for decision in simulated)
    correct_positive = sum(
        item["top_candidate_expected"] and decision == "allow"
        for item, decision in positives
    )
    false_recalls = sum(decision == "allow" for _, decision in negatives)
    no_recalls = len(results) - allows
    correct_no_recall = sum(decision != "allow" for _, decision in negatives)
    def safety(category: str, tag: Optional[str] = None) -> float:
        items = [
            decision for item, decision in zip(results, simulated)
            if item["category"] == category and (tag is None or tag in item["tags"])
        ]
        return _percent(sum(decision != "allow" for decision in items), len(items))
    return {
        "base_min_score": round(base_min_score, 2),
        "correct_positive_recall_count": correct_positive,
        "correct_positive_recall_rate": _percent(correct_positive, len(positives)),
        "false_recall_count": false_recalls,
        "false_recall_rate": _percent(false_recalls, len(negatives)),
        "no_recall_precision": _percent(correct_no_recall, no_recalls),
        "simulated_allow_rate": _percent(allows, len(results)),
        "stale_inactive_safety": safety("stale", "old"),
        "low_info_safety": safety("low_info"),
        "high_risk_suppression": safety("high_risk"),
    }


def counterfactual_parity(results: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    entering_gate = [item for item in results if not item["pre_gate"]]
    mismatches: List[str] = []
    for item in entering_gate:
        simulated = _simulated_gate_decision(item, base_min_score=BASE_MIN_SCORE)
        if simulated != item["real_gate_decision"]:
            mismatches.append(str(item["case_id"]))
    return {
        "base_min_score": BASE_MIN_SCORE,
        "checked_cases": len(entering_gate),
        "matching_cases": len(entering_gate) - len(mismatches),
        "parity": _percent(len(entering_gate) - len(mismatches), len(entering_gate)),
        "mismatch_case_ids": mismatches,
    }


def _threshold_sweep(results: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    steps = int(round((SWEEP_END - SWEEP_START) / SWEEP_STEP))
    return [
        _simulation_metrics(results, base_min_score=round(SWEEP_START + index * SWEEP_STEP, 2))
        for index in range(steps + 1)
    ]


def _safe_frontier(sweep: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    safe = [
        item for item in sweep
        if item["false_recall_rate"] == 0.0
        and item["stale_inactive_safety"] == 1.0
        and item["low_info_safety"] == 1.0
        and item["high_risk_suppression"] == 1.0
    ]
    current = next(item for item in sweep if item["base_min_score"] == BASE_MIN_SCORE)
    best = None
    if safe:
        # Prefer higher threshold on a tie: it keeps a more conservative gate.
        best = sorted(
            safe,
            key=lambda item: (item["correct_positive_recall_count"], item["base_min_score"]),
            reverse=True,
        )[0]
    return {
        "safe_thresholds": [item["base_min_score"] for item in safe],
        "current": current,
        "best_zero_false_recall_candidate": best,
        "recall_delta": round(
            (best["correct_positive_recall_rate"] - current["correct_positive_recall_rate"]), 4,
        ) if best else None,
        "additional_allows": (
            best["correct_positive_recall_count"] - current["correct_positive_recall_count"]
        ) if best else None,
    }


def _ablations(results: Sequence[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    baseline = _simulation_metrics(results, base_min_score=BASE_MIN_SCORE)
    output: Dict[str, Dict[str, Any]] = {}
    for blocker in BLOCKERS:
        metrics = _simulation_metrics(
            results, base_min_score=BASE_MIN_SCORE, ignored_blocker=blocker,
        )
        output[blocker] = {
            "recovered_correct_positive": (
                metrics["correct_positive_recall_count"]
                - baseline["correct_positive_recall_count"]
            ),
            "new_false_recall_count": (
                metrics["false_recall_count"] - baseline["false_recall_count"]
            ),
            "false_recall_rate": metrics["false_recall_rate"],
            "delta_no_recall_precision": round(
                metrics["no_recall_precision"] - baseline["no_recall_precision"], 4,
            ),
        }
    return output


def _blocker_analysis(results: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    clean = [
        item for item in results
        if item.get("positive_partition") == "gate_blocked_expected_top"
    ]
    multi = Counter()
    primary = Counter()
    combinations = Counter()
    for item in clean:
        blockers = item["production_blockers"]
        multi.update(blockers)
        primary_value = primary_blocker(blockers)
        if primary_value:
            primary[primary_value] += 1
        combinations[" + ".join(blockers) if blockers else "no_reported_blocker"] += 1
    return {
        "clean_gate_false_negative_count": len(clean),
        "multi_label_counts": dict(sorted(multi.items())),
        "primary_blocker_counts": dict(sorted(primary.items())),
        "combinations": [
            {"blockers": name, "count": count}
            for name, count in sorted(combinations.items(), key=lambda item: (-item[1], item[0]))
        ],
    }


def _coverage(results: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    counts = Counter(reason for item in results for reason in item["production_blockers"])
    return {
        blocker: {
            "exercised_cases": counts[blocker],
            "coverage": "covered" if counts[blocker] else "benchmark has insufficient coverage for this rule",
        }
        for blocker in BLOCKERS
    }


def _explicit_analysis(results: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    explicit = [
        item for item in results if item["should_recall"] and item["explicit_request"]
    ]
    partition = Counter(item["positive_partition"] for item in explicit)
    clean = [
        item for item in explicit
        if item["positive_partition"] == "gate_blocked_expected_top"
    ]
    blockers = Counter(reason for item in clean for reason in item["production_blockers"])
    retrieval_or_ranking = sum(
        partition[name] for name in ("retrieval_miss", "ranking_miss")
    )
    return {
        "explicit_positive_total": len(explicit),
        "success_count": partition["success_allow_expected"],
        "clean_gate_false_negative_count": partition["gate_blocked_expected_top"],
        "retrieval_or_ranking_miss_count": retrieval_or_ranking,
        "blocker_counts": dict(sorted(blockers.items())),
        "has_relevance_below_threshold": bool(blockers["relevance_below_threshold"]),
        "has_low_memory_quality": bool(blockers["low_memory_quality"]),
    }


def _positive_partition(results: Sequence[Dict[str, Any]]) -> Dict[str, int]:
    names = (
        "pre_gate_positive_skip", "retrieval_miss", "ranking_miss",
        "gate_blocked_expected_top", "success_allow_expected", "inconsistent_return",
    )
    counts = Counter(item["positive_partition"] for item in results if item["should_recall"])
    return {name: counts[name] for name in names}


def run_analysis(
    seed_path: Path,
    cases_path: Path,
    *,
    validate_dataset: bool = True,
) -> Dict[str, Any]:
    """Run the full analysis and reject a drifted 0.48 simulator."""
    if validate_dataset:
        validate_dataset_hashes(seed_path, cases_path)
    results = _run_cases(seed_path, cases_path)
    parity = counterfactual_parity(results)
    if parity["parity"] != 1.0:
        raise RuntimeError(
            "counterfactual baseline parity failed: %.2f%% (%s)" % (
                parity["parity"] * 100, ", ".join(parity["mismatch_case_ids"]),
            )
        )
    partition = _positive_partition(results)
    positive_count = sum(1 for item in results if item["should_recall"])
    if sum(partition.values()) != positive_count:
        raise RuntimeError("positive partition is not exhaustive")
    sweep = _threshold_sweep(results)
    blockers = _blocker_analysis(results)
    clean = [
        item for item in results
        if item.get("positive_partition") == "gate_blocked_expected_top"
    ]
    return {
        "schema_version": ANALYSIS_SCHEMA_VERSION,
        "dataset_version": DATASET_VERSION,
        "dataset": {
            "seed_sha256": _sha256(seed_path),
            "cases_sha256": _sha256(cases_path),
            "case_count": len(results),
        },
        "configuration": {
            "base_min_score": BASE_MIN_SCORE,
            "explicit_threshold_adjustment": -0.12,
            "high_risk_threshold_adjustment": 0.10,
            "production_gate_changed": False,
        },
        "positive_case_count": positive_count,
        "positive_partition": partition,
        "negative_outcomes": dict(sorted(Counter(
            item.get("negative_outcome") for item in results if not item["should_recall"]
        ).items())),
        "case_diagnostics": results,
        "gate_blockers": blockers,
        "score_distributions": _score_distributions(results),
        "clean_gate_fn_score_margin_buckets": margin_buckets(clean),
        "clean_gate_fn_margin_interpretation": {
            "within_0_01_to_0_05_below_threshold": sum(
                item["score_margin"] is not None
                and -0.05 <= item["score_margin"] <= -0.01 for item in clean
            ),
            "more_than_0_20_below_threshold": sum(
                item["score_margin"] is not None and item["score_margin"] < -0.20
                for item in clean
            ),
            "at_or_above_threshold_with_other_blocker": sum(
                item["score_margin"] is not None and item["score_margin"] >= 0
                for item in clean
            ),
        },
        "explicit_recall": _explicit_analysis(results),
        "baseline_parity": parity,
        "threshold_sweep": sweep,
        "zero_false_recall_safe_frontier": _safe_frontier(sweep),
        "one_at_a_time_ablation": _ablations(results),
        "coverage": _coverage(results),
        "privacy": {
            "query_text_exported": False,
            "raw_memory_text_exported": False,
            "identity_exported": False,
            "conversation_key_exported": False,
        },
    }


def _markdown_table(rows: Sequence[Sequence[Any]], headers: Sequence[str]) -> List[str]:
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---:" for _ in headers) + " |"]
    lines.extend("| " + " | ".join(str(value) for value in row) + " |" for row in rows)
    return lines


def markdown_report(report: Dict[str, Any]) -> str:
    """Render a compact, metadata-only report suitable for checked artifacts."""
    dataset = report["dataset"]
    partition = report["positive_partition"]
    blockers = report["gate_blockers"]
    frontier = report["zero_false_recall_safe_frontier"]
    parity = report["baseline_parity"]
    lines = [
        "# Memory V5-B.6 Recall Gate Analysis", "",
        "## Executive Summary", "",
        "This offline report analyzes the real V5 Exact + FTS + Recall Gate path in an isolated SQLite database. No production Recall Gate thresholds or blocker rules were changed in V5-B.6.",
        "", "## Dataset integrity", "",
        "- Dataset: `%s`" % report["dataset_version"],
        "- Cases: %s" % dataset["case_count"],
        "- Seed SHA256: `%s`" % dataset["seed_sha256"],
        "- Cases SHA256: `%s`" % dataset["cases_sha256"],
        "", "## Baseline parity", "",
        "- Base threshold: %.2f" % BASE_MIN_SCORE,
        "- Counterfactual parity: %.2f%% (%d/%d Gate cases)" % (
            parity["parity"] * 100, parity["matching_cases"], parity["checked_cases"],
        ),
        "", "## Positive failure partition", "",
    ]
    lines.extend(_markdown_table(
        [(name, count) for name, count in partition.items()], ["Bucket", "Cases"],
    ))
    lines.extend(["", "## Gate blocker decomposition", ""])
    lines.extend(_markdown_table(
        [(name, count) for name, count in blockers["multi_label_counts"].items()],
        ["Blocker (multi-label)", "Clean Gate FN cases"],
    ))
    lines.extend(["", "## Blocker combinations", ""])
    lines.extend(_markdown_table(
        [(item["blockers"], item["count"]) for item in blockers["combinations"]],
        ["Combination", "Cases"],
    ))
    lines.extend(["", "## Score / margin distribution", ""])
    for name, values in report["score_distributions"].items():
        margin = values["score_margin"]
        lines.append("- %s (n=%d): margin min/P25/P50/P75/max = %s / %s / %s / %s / %s" % (
            name, values["count"], margin["min"], margin["p25"], margin["p50"],
            margin["p75"], margin["max"],
        ))
    lines.extend(["", "## Explicit recall analysis", ""])
    explicit = report["explicit_recall"]
    lines.append("- explicit positives=%d; success=%d; clean Gate FN=%d; retrieval/ranking misses=%d" % (
        explicit["explicit_positive_total"], explicit["success_count"],
        explicit["clean_gate_false_negative_count"], explicit["retrieval_or_ranking_miss_count"],
    ))
    lines.extend(["", "## Threshold sweep", ""])
    lines.extend(_markdown_table([
        (item["base_min_score"], item["correct_positive_recall_count"],
         "%.2f%%" % (item["correct_positive_recall_rate"] * 100),
         item["false_recall_count"], "%.2f%%" % (item["false_recall_rate"] * 100))
        for item in report["threshold_sweep"]
    ], ["Base", "Correct positive", "Recall", "False recalls", "FR rate"]))
    lines.extend(["", "## Zero-False-Recall safe frontier", ""])
    lines.append("- Safe thresholds: %s" % (
        ", ".join("%.2f" % value for value in frontier["safe_thresholds"]) or "none",
    ))
    best = frontier["best_zero_false_recall_candidate"]
    if best:
        lines.append("- Future experiment candidate: %.2f; recall delta vs 0.48: %+0.2f pp; additional correct allows: %d." % (
            best["base_min_score"], frontier["recall_delta"] * 100, frontier["additional_allows"],
        ))
    else:
        lines.append("- No zero-false-recall safe frontier exists in the configured sweep.")
    lines.extend(["", "## One-at-a-time ablation", ""])
    lines.extend(_markdown_table([
        (name, values["recovered_correct_positive"], values["new_false_recall_count"],
         "%.2f pp" % (values["delta_no_recall_precision"] * 100))
        for name, values in report["one_at_a_time_ablation"].items()
    ], ["Ignored blocker", "Recovered correct positives", "New false recalls", "Δ no-recall precision"]))
    lines.extend(["", "## Coverage gaps", ""])
    for blocker, details in report["coverage"].items():
        lines.append("- %s: %d exercised cases; %s." % (
            blocker, details["exercised_cases"], details["coverage"],
        ))
    lines.extend([
        "", "## Recommendation", "",
        "The safe frontier is an offline future-experiment candidate only; it does not change production configuration. Proceed to a controlled V5-B.7 Gate experiment only if the reported frontier improves recall while all listed safety checks remain at 100%.",
        "", "## Deferred work", "",
        "Real Recall remains shadow-only. Vector candidate generation, sqlite-vec, embeddings, RRF, and reranking are deferred.",
        "", "## Privacy", "",
        "The JSON and Markdown exports contain no query text, raw memory text, identity, conversation key, or chat transcript.",
    ])
    return "\n".join(lines) + "\n"


def main(argv: Optional[Sequence[str]] = None) -> int:
    repo = Path(__file__).resolve().parents[3]
    parser = argparse.ArgumentParser(description="Run offline V5-B.6 Recall Gate analysis.")
    parser.add_argument("--seed", type=Path, default=repo / "social_layer/tests/fixtures/memory_recall_seed.jsonl")
    parser.add_argument("--cases", type=Path, default=repo / "social_layer/tests/fixtures/memory_recall_cases.json")
    parser.add_argument("--json-output", type=Path)
    parser.add_argument("--report-output", type=Path)
    parser.add_argument(
        "--allow-unverified-dataset", action="store_true",
        help="analyze an independently versioned private fixture without V5-B.5 hash validation",
    )
    args = parser.parse_args(argv)
    try:
        report = run_analysis(
            args.seed, args.cases,
            validate_dataset=not args.allow_unverified_dataset,
        )
    except (RuntimeError, ValueError) as error:
        parser.error(str(error))
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(rendered + "\n", encoding="utf-8")
    if args.report_output:
        args.report_output.parent.mkdir(parents=True, exist_ok=True)
        args.report_output.write_text(markdown_report(report), encoding="utf-8")
    print(json.dumps({
        "positive_partition": report["positive_partition"],
        "baseline_parity": report["baseline_parity"],
        "safe_frontier": report["zero_false_recall_safe_frontier"]["safe_thresholds"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
