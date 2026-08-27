"""Bounded, privacy-safe V5-B.7 Recall Gate shadow experiment support."""

from __future__ import annotations

import json
import sqlite3
import tempfile
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .memory_recall_gate import RecallCandidate


EXPERIMENT_OFF = "off"
EXPERIMENT_V5B7_GATE_036 = "v5b7-gate-036"
EXPERIMENT_MAX_EVENTS = 10000
EXPERIMENT_WRITE_BUSY_TIMEOUT_MS = 75
_NON_RELEVANCE_BLOCKERS = {
    "low_information_turn", "emotion_non_interruption", "moment_non_interruption",
    "high_risk_topic", "conversation_cooldown", "same_memory_cooldown",
    "low_memory_quality",
}
_BASELINE_BLOCKERS = _NON_RELEVANCE_BLOCKERS | {"relevance_below_threshold"}
_SCHEMA = """
CREATE TABLE IF NOT EXISTS memory_gate_experiment_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    experiment_name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    baseline_base_threshold REAL NOT NULL,
    candidate_base_threshold REAL NOT NULL,
    baseline_decision TEXT NOT NULL,
    candidate_decision TEXT NOT NULL,
    outcome TEXT NOT NULL,
    candidate_layer TEXT,
    candidate_kind TEXT,
    score REAL,
    baseline_threshold REAL,
    candidate_threshold REAL,
    score_margin_baseline REAL,
    score_margin_candidate REAL,
    explicit_request INTEGER NOT NULL DEFAULT 0,
    baseline_blockers_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_memory_gate_experiment_events_name_id
ON memory_gate_experiment_events(experiment_name, id DESC);
"""


@dataclass(frozen=True)
class GateExperimentProfile:
    name: str
    candidate_base_min_score: float


@dataclass(frozen=True)
class GateExperimentDecision:
    candidate_decision: str
    outcome: str
    candidate_threshold: Optional[float]
    blockers: tuple[str, ...]


def resolve_gate_experiment(value: Optional[str]) -> Optional[GateExperimentProfile]:
    """Resolve only audited named profiles; arbitrary score overrides are invalid."""
    normalized = (value or EXPERIMENT_OFF).strip().casefold()
    if normalized == EXPERIMENT_OFF:
        return None
    if normalized == EXPERIMENT_V5B7_GATE_036:
        return GateExperimentProfile(EXPERIMENT_V5B7_GATE_036, 0.36)
    raise ValueError("invalid recall gate experiment: %s" % value)


def threshold_for(
    base_min_score: float, explicit_request: bool, high_risk: bool,
) -> float:
    threshold = float(base_min_score) - (0.12 if explicit_request else 0.0)
    if high_risk:
        threshold += 0.10
    return min(0.95, max(0.20, threshold))


def _blockers(reasons: Iterable[str]) -> tuple[str, ...]:
    return tuple(reason for reason in reasons if reason in _NON_RELEVANCE_BLOCKERS)


def _baseline_blockers(reasons: Iterable[str]) -> tuple[str, ...]:
    return tuple(reason for reason in reasons if reason in _BASELINE_BLOCKERS)


def evaluate_counterfactual(
    *,
    candidate: Optional[RecallCandidate],
    baseline_decision: str,
    explicit_request: bool,
    high_risk: bool,
    production_reasons: Sequence[str],
    candidate_base_min_score: float,
    pre_gate: bool = False,
) -> GateExperimentDecision:
    """Evaluate a candidate threshold without mutating the production Gate.

    All real non-relevance blockers are copied as facts from the real decision;
    only the relevance comparison is recomputed at the named profile threshold.
    """
    if pre_gate:
        return GateExperimentDecision("skip", "not_evaluated_pre_gate", None, ())
    if candidate is None:
        outcome = "unchanged_none" if baseline_decision == "none" else "inconsistent"
        return GateExperimentDecision("none", outcome, None, ())
    candidate_threshold = threshold_for(
        candidate_base_min_score, explicit_request, high_risk,
    )
    blockers = list(_blockers(production_reasons))
    if candidate.score < candidate_threshold:
        blockers.append("relevance_below_threshold")
    candidate_decision = "blocked" if blockers else "allow"
    if baseline_decision == "allow":
        outcome = "unchanged_allow" if candidate_decision == "allow" else "inconsistent"
    elif baseline_decision == "blocked":
        outcome = (
            "recovered_by_candidate"
            if candidate_decision == "allow" else "unchanged_blocked"
        )
    elif baseline_decision == "none":
        outcome = "inconsistent"
    else:
        outcome = "inconsistent"
    return GateExperimentDecision(
        candidate_decision, outcome, candidate_threshold, tuple(blockers),
    )


class GateExperimentStore:
    """Independent bounded audit store with no identity or text columns."""

    def __init__(self, db_path: Path, *, max_events: int = EXPERIMENT_MAX_EVENTS) -> None:
        self.db_path = Path(db_path).expanduser().resolve()
        self.max_events = max(1, min(EXPERIMENT_MAX_EVENTS, int(max_events)))
        with closing(self._connect()) as connection:
            connection.executescript(_SCHEMA)
            connection.commit()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.db_path, timeout=EXPERIMENT_WRITE_BUSY_TIMEOUT_MS / 1000.0,
        )
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA busy_timeout = %d" % EXPERIMENT_WRITE_BUSY_TIMEOUT_MS)
        return connection

    def record(
        self,
        *,
        profile: GateExperimentProfile,
        baseline_base_threshold: float,
        baseline_decision: str,
        baseline_threshold: Optional[float],
        explicit_request: bool,
        production_reasons: Sequence[str],
        candidate: Optional[RecallCandidate],
        evaluation: GateExperimentDecision,
    ) -> None:
        score = candidate.score if candidate is not None else None
        candidate_threshold = evaluation.candidate_threshold
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO memory_gate_experiment_events(
                    experiment_name, created_at, baseline_base_threshold,
                    candidate_base_threshold, baseline_decision, candidate_decision,
                    outcome, candidate_layer, candidate_kind, score,
                    baseline_threshold, candidate_threshold, score_margin_baseline,
                    score_margin_candidate, explicit_request, baseline_blockers_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    profile.name, datetime.now(timezone.utc).isoformat(),
                    float(baseline_base_threshold), profile.candidate_base_min_score,
                    baseline_decision, evaluation.candidate_decision, evaluation.outcome,
                    candidate.layer if candidate is not None else None,
                    candidate.kind if candidate is not None else None,
                    score, baseline_threshold, candidate_threshold,
                    score - baseline_threshold if score is not None and baseline_threshold is not None else None,
                    score - candidate_threshold if score is not None and candidate_threshold is not None else None,
                    1 if explicit_request else 0,
                    json.dumps(list(_baseline_blockers(production_reasons)), ensure_ascii=False),
                ),
            )
            connection.execute(
                """
                DELETE FROM memory_gate_experiment_events
                WHERE id IN (
                    SELECT id FROM memory_gate_experiment_events
                    WHERE experiment_name = ?
                    ORDER BY id DESC LIMIT -1 OFFSET ?
                )
                """,
                (profile.name, self.max_events),
            )
            connection.commit()


def run_fixed_corpus_guardrail(seed_path: Path, cases_path: Path) -> Dict[str, Any]:
    """Run the B.7 evaluator case-by-case and compare it to the B.6 simulator.

    This deliberately does not use B.6's aggregate result as the B.7 outcome:
    it seeds a new isolated service, executes the real 0.52 Gate once per case,
    and supplies those real candidates/reasons to ``evaluate_counterfactual``.
    B.6 is used only as an independently calculated per-case parity oracle.
    """
    # Imports are local to avoid a runtime import cycle: MemoryService imports
    # this module, while the offline B.6 harness imports MemoryService.
    from .memory_benchmark import (
        DEFAULT_USER_ID, _expected_ids, load_cases, load_seed, seed_service,
    )
    from .memory_gate_analysis import (
        _fixture_id, _ranked_candidates, _simulated_gate_decision, run_analysis,
    )
    from .memory_service.core import MemoryService, MemoryServiceConfig

    profile = resolve_gate_experiment(EXPERIMENT_V5B7_GATE_036)
    if profile is None:
        raise RuntimeError("B.7 fixed guardrail profile is unavailable")
    b6_report = run_analysis(seed_path, cases_path)
    b6_cases = {item["case_id"]: item for item in b6_report["case_diagnostics"]}
    records = load_seed(seed_path)
    cases = load_cases(cases_path)
    outcomes: List[Dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="yogurt-memory-gate-experiment-") as directory:
        service = MemoryService(MemoryServiceConfig(Path(directory) / "memory.sqlite3"))
        source_map = seed_service(service, records)
        for case in cases:
            conversation_key = "gate-experiment-" + str(case["id"])
            response = service.recall(
                persona_id="yogurt", user_id=DEFAULT_USER_ID,
                surface=case["surface"], conversation_key=conversation_key,
                message=case["query"],
            )
            pre_gate = response.get("decision") == "skip"
            ranked = [] if pre_gate else _ranked_candidates(
                service, query=case["query"], surface=case["surface"],
                conversation_key=conversation_key,
            )
            scoped_key = service.scoped_conversation_key(case["surface"], conversation_key)
            events = service.gate.events(
                user_id=DEFAULT_USER_ID, conversation_key=scoped_key, limit=1,
            )
            event = events[0] if events else None
            baseline_decision = "skip" if pre_gate else (
                str(event["decision"]) if event is not None else "none"
            )
            reasons = list(event.get("reasons", [])) if event is not None else []
            candidate = ranked[0] if ranked else None
            evaluation = evaluate_counterfactual(
                candidate=candidate, baseline_decision=baseline_decision,
                explicit_request=bool(event["explicit_request"]) if event else False,
                high_risk=service.gate._high_risk_topic(case["query"]),
                production_reasons=reasons,
                candidate_base_min_score=profile.candidate_base_min_score,
                pre_gate=pre_gate,
            )
            ranked_ids = [
                fixture_id for fixture_id in
                (_fixture_id(item, source_map) for item in ranked)
                if fixture_id is not None
            ]
            expected = _expected_ids(case)
            top_expected = bool(ranked_ids and ranked_ids[0] in expected)
            b6_case = b6_cases[str(case["id"])]
            b6_decision = _simulated_gate_decision(
                b6_case, base_min_score=profile.candidate_base_min_score,
            )
            outcomes.append({
                "case_id": case["id"], "category": case["category"],
                "tags": list(case.get("tags", [])),
                "should_recall": bool(case["should_recall"]),
                "top_candidate_expected": top_expected,
                "candidate_decision": evaluation.candidate_decision,
                "b6_candidate_decision": b6_decision,
            })

    positives = [item for item in outcomes if item["should_recall"]]
    negatives = [item for item in outcomes if not item["should_recall"]]
    allow = lambda item: item["candidate_decision"] == "allow"
    def safety(category: str, tag: Optional[str] = None) -> float:
        selected = [
            item for item in outcomes
            if item["category"] == category and (tag is None or tag in item["tags"])
        ]
        return (
            round(float(sum(not allow(item) for item in selected)) / len(selected), 4)
            if selected else 0.0
        )
    mismatches = [
        str(item["case_id"]) for item in outcomes
        if item["candidate_decision"] != item["b6_candidate_decision"]
    ]
    correct_positive = sum(item["top_candidate_expected"] and allow(item) for item in positives)
    false_recall = sum(allow(item) for item in negatives)
    b6_candidate = next(
        item for item in b6_report["threshold_sweep"]
        if item["base_min_score"] == profile.candidate_base_min_score
    )
    candidate_metrics = {
        "correct_positive_recall_count": correct_positive,
        "correct_positive_recall_rate": round(float(correct_positive) / len(positives), 4),
        "false_recall_count": false_recall,
        "false_recall_rate": round(float(false_recall) / len(negatives), 4),
        "stale_inactive_safety": safety("stale", "old"),
        "low_info_safety": safety("low_info"),
        "high_risk_suppression": safety("high_risk"),
    }
    return {
        "dataset_version": b6_report["dataset_version"],
        "candidate_base_min_score": profile.candidate_base_min_score,
        "baseline_base_min_score": service.gate.min_score,
        "case_count": len(outcomes),
        "decision_parity": {
            "checked_cases": len(outcomes),
            "matching_cases": len(outcomes) - len(mismatches),
            "parity": round(float(len(outcomes) - len(mismatches)) / len(outcomes), 4),
            "mismatch_case_ids": mismatches,
        },
        "candidate_metrics": candidate_metrics,
        "b6_candidate_metrics": {
            key: b6_candidate[key] for key in candidate_metrics
        },
    }
