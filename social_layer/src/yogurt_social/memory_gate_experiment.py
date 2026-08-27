"""Bounded, privacy-safe V5-B.7 Recall Gate shadow experiment support."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

from .memory_recall_gate import RecallCandidate


EXPERIMENT_OFF = "off"
EXPERIMENT_V5B7_GATE_036 = "v5b7-gate-036"
EXPERIMENT_MAX_EVENTS = 10000
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
        connection = sqlite3.connect(self.db_path)
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA busy_timeout = 3000")
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
