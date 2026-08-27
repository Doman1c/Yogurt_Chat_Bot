"""Aggregate-only report for V5-B.7 Recall Gate shadow audit events."""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Optional, Sequence


def _percent(numerator: int, denominator: int) -> float:
    return round(float(numerator) / denominator, 4) if denominator else 0.0


def _distribution(values: Sequence[float]) -> Dict[str, Optional[float]]:
    ordered = sorted(float(value) for value in values)
    def percentile(value: int) -> Optional[float]:
        if not ordered:
            return None
        index = max(0, math.ceil((value / 100.0) * len(ordered)) - 1)
        return round(ordered[index], 6)
    return {
        "min": percentile(0), "p10": percentile(10), "p25": percentile(25),
        "p50": percentile(50), "p75": percentile(75), "p90": percentile(90),
        "max": percentile(100),
    }


def summarize_experiment(db_path: Path, experiment: str) -> Dict[str, Any]:
    """Read only aggregate-safe audit fields, never identities or text."""
    database = Path(db_path).expanduser().resolve()
    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            ("memory_gate_experiment_events",),
        ).fetchone()
        if table is None:
            rows = []
        else:
            rows = connection.execute(
                """
                SELECT baseline_decision, candidate_decision, outcome,
                       candidate_kind, explicit_request, baseline_blockers_json,
                       score_margin_baseline, score_margin_candidate
                FROM memory_gate_experiment_events
                WHERE experiment_name = ?
                """,
                (experiment,),
            ).fetchall()
    baseline = Counter()
    candidate = Counter()
    outcomes = Counter()
    kinds = Counter()
    explicit = Counter()
    blockers = Counter()
    baseline_margins = []
    candidate_margins = []
    for row in rows:
        baseline[str(row["baseline_decision"])] += 1
        candidate[str(row["candidate_decision"])] += 1
        outcomes[str(row["outcome"])] += 1
        kinds[str(row["candidate_kind"] or "none")] += 1
        explicit["explicit" if bool(row["explicit_request"]) else "non_explicit"] += 1
        if row["score_margin_baseline"] is not None:
            baseline_margins.append(float(row["score_margin_baseline"]))
        if row["score_margin_candidate"] is not None:
            candidate_margins.append(float(row["score_margin_candidate"]))
        try:
            reasons = json.loads(row["baseline_blockers_json"])
        except (TypeError, json.JSONDecodeError):
            reasons = []
        if isinstance(reasons, list):
            blockers.update(str(reason) for reason in reasons)
    total = len(rows)
    recovered = outcomes["recovered_by_candidate"]
    pre_gate = baseline["skip"]
    gate_evaluated = total - pre_gate
    return {
        "schema_version": 1,
        "experiment": experiment,
        "total_audit_events": total,
        "pre_gate_skipped_events": pre_gate,
        "gate_evaluated_events": gate_evaluated,
        "baseline": {
            "allow": baseline["allow"], "blocked": baseline["blocked"],
            "none": baseline["none"], "pre_gate_skipped": baseline["skip"],
        },
        "candidate": {
            "allow": candidate["allow"], "blocked": candidate["blocked"],
            "none": candidate["none"],
        },
        "outcomes": {
            "unchanged_allow": outcomes["unchanged_allow"],
            "unchanged_blocked": outcomes["unchanged_blocked"],
            "recovered_by_candidate": recovered,
            "unchanged_none": outcomes["unchanged_none"],
            "not_evaluated_pre_gate": outcomes["not_evaluated_pre_gate"],
            "inconsistent": outcomes["inconsistent"],
        },
        "candidate_kind": dict(sorted(kinds.items())),
        "explicit": dict(sorted(explicit.items())),
        "blocker_distribution": dict(sorted(blockers.items())),
        "score_margin_distribution": {
            "baseline": _distribution(baseline_margins),
            "candidate": _distribution(candidate_margins),
        },
        "gate_candidate_recovery": {
            "gate_recovered_by_candidate_count": recovered,
            "gate_evaluated_events_denominator": gate_evaluated,
            "gate_recovered_by_candidate_rate": _percent(recovered, gate_evaluated),
        },
        "privacy": {
            "identity_exported": False, "conversation_key_exported": False,
            "query_text_exported": False, "memory_text_exported": False,
            "prompt_text_exported": False, "chat_history_exported": False,
        },
        "interpretation": (
            "Real shadow traffic is unlabeled. Gate-level candidate recovery is not equivalent to correctness or token-budget passage."
        ),
    }


def markdown_report(summary: Dict[str, Any]) -> str:
    lines = [
        "# V5-B.7 Recall Gate Shadow Experiment", "",
        "## Aggregate summary", "",
        "- Experiment: `%s`" % summary["experiment"],
        "- Total audit events: %d" % summary["total_audit_events"],
        "- Pre-gate skipped events: %d" % summary["pre_gate_skipped_events"],
        "- Gate-evaluated events: %d" % summary["gate_evaluated_events"],
        "- Gate recovered by candidate: %d / %d (%.2f%%)" % (
            summary["gate_candidate_recovery"]["gate_recovered_by_candidate_count"],
            summary["gate_candidate_recovery"]["gate_evaluated_events_denominator"],
            summary["gate_candidate_recovery"]["gate_recovered_by_candidate_rate"] * 100,
        ),
        "", "## Decisions", "",
        "- Baseline: %s" % json.dumps(summary["baseline"], sort_keys=True),
        "- Candidate: %s" % json.dumps(summary["candidate"], sort_keys=True),
        "- Outcomes: %s" % json.dumps(summary["outcomes"], sort_keys=True),
        "", "## Candidate categories and blockers", "",
        "- Candidate kinds: %s" % json.dumps(summary["candidate_kind"], sort_keys=True),
        "- Explicit split: %s" % json.dumps(summary["explicit"], sort_keys=True),
        "- Baseline blockers: %s" % json.dumps(summary["blocker_distribution"], sort_keys=True),
        "", "## Score margins", "",
        "- Baseline: %s" % json.dumps(summary["score_margin_distribution"]["baseline"], sort_keys=True),
        "- Candidate: %s" % json.dumps(summary["score_margin_distribution"]["candidate"], sort_keys=True),
        "", "## Interpretation limit", "",
        summary["interpretation"],
        "", "## Privacy", "",
        "This aggregate-only export contains no identity, conversation key, query, memory text, prompt, or chat history.",
    ]
    return "\n".join(lines) + "\n"


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Report V5-B.7 shadow experiment aggregates.")
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--experiment", required=True)
    parser.add_argument("--json-output", type=Path)
    parser.add_argument("--report-output", type=Path)
    args = parser.parse_args(argv)
    try:
        summary = summarize_experiment(args.db, args.experiment)
    except (OSError, sqlite3.Error, ValueError) as error:
        parser.error(str(error))
    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    if args.report_output:
        args.report_output.parent.mkdir(parents=True, exist_ok=True)
        args.report_output.write_text(markdown_report(summary), encoding="utf-8")
    print(markdown_report(summary), end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
