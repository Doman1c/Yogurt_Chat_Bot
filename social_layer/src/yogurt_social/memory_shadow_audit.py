"""Aggregate the V4.2/V5 shadow recall audit without exporting raw text."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Sequence


def _parse_bound(value: Optional[str], field: str) -> Optional[str]:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("%s must be an ISO-8601 timestamp" % field)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def _score_bucket(score: Optional[float]) -> str:
    if score is None:
        return "none"
    bounded = min(0.999, max(0.0, float(score)))
    lower = int(bounded * 5) / 5.0
    return "%.1f-%.1f" % (lower, lower + 0.2)


def summarize_shadow_audit(
    db_path: Path,
    *,
    since: Optional[str] = None,
    until: Optional[str] = None,
) -> Dict[str, Any]:
    """Return aggregate gate telemetry only.

    This intentionally never selects user IDs, conversation keys, memory text,
    prompts, or any stored state text.  The audit table does not contain raw
    message text, and this function preserves that privacy boundary.
    """
    start = _parse_bound(since, "since")
    end = _parse_bound(until, "until")
    if start is not None and end is not None and start > end:
        raise ValueError("since must not be after until")
    clauses = []
    values = []
    if start is not None:
        clauses.append("created_at >= ?")
        values.append(start)
    if end is not None:
        clauses.append("created_at <= ?")
        values.append(end)
    query = "SELECT decision, candidate_layer, candidate_kind, score, reasons_json, conversation_key FROM memory_recall_events"
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    decisions: Counter = Counter()
    reasons: Counter = Counter()
    kinds: Counter = Counter()
    buckets: Counter = Counter()
    surfaces: Counter = Counter()
    with sqlite3.connect(Path(db_path).expanduser().resolve()) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(query, tuple(values)).fetchall()
    for row in rows:
        decisions[str(row["decision"])] += 1
        candidate = "%s:%s" % (row["candidate_layer"] or "none", row["candidate_kind"] or "none")
        kinds[candidate] += 1
        buckets[_score_bucket(row["score"])] += 1
        key = str(row["conversation_key"])
        surface = key.split(":", 1)[0] if ":" in key else "unknown"
        surfaces[surface] += 1
        try:
            row_reasons = json.loads(row["reasons_json"])
        except (TypeError, json.JSONDecodeError):
            row_reasons = []
        if isinstance(row_reasons, list):
            for reason in row_reasons:
                if isinstance(reason, str):
                    reasons[reason] += 1
    total = len(rows)
    return {
        "schema_version": 1,
        "turn_count": total,
        "allow_count": decisions["allow"],
        "allow_rate": round(float(decisions["allow"]) / total, 4) if total else 0.0,
        "decisions": dict(sorted(decisions.items())),
        "block_reasons": dict(sorted((key, value) for key, value in reasons.items() if key != "gate_allow")),
        "candidate_kinds": dict(sorted(kinds.items())),
        "score_buckets": dict(sorted(buckets.items())),
        "surface_split": dict(sorted(surfaces.items())),
        "privacy": {"raw_message_exported": False, "raw_memory_text_exported": False},
    }


def _format(summary: Dict[str, Any]) -> str:
    lines = [
        "Memory Shadow Audit Summary",
        "Turns: %d" % summary["turn_count"],
        "Gate allow: %d (%.2f%%)" % (summary["allow_count"], summary["allow_rate"] * 100),
        "Decisions: %s" % json.dumps(summary["decisions"], ensure_ascii=False, sort_keys=True),
        "Block reasons: %s" % json.dumps(summary["block_reasons"], ensure_ascii=False, sort_keys=True),
        "Candidate kinds: %s" % json.dumps(summary["candidate_kinds"], ensure_ascii=False, sort_keys=True),
        "Score buckets: %s" % json.dumps(summary["score_buckets"], ensure_ascii=False, sort_keys=True),
        "Surfaces: %s" % json.dumps(summary["surface_split"], ensure_ascii=False, sort_keys=True),
        "Privacy: raw messages and memory text are not exported.",
    ]
    return "\n".join(lines) + "\n"


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Summarize privacy-safe memory shadow audit events.")
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--since")
    parser.add_argument("--until")
    parser.add_argument("--days", type=int)
    parser.add_argument("--json-output", type=Path)
    args = parser.parse_args(argv)
    if args.days is not None:
        if args.days < 0:
            parser.error("--days must be non-negative")
        if args.since is not None:
            parser.error("--days cannot be combined with --since")
        args.since = (datetime.now(timezone.utc) - timedelta(days=args.days)).isoformat()
    try:
        summary = summarize_shadow_audit(args.db, since=args.since, until=args.until)
    except (OSError, sqlite3.Error, ValueError) as error:
        parser.error(str(error))
    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(_format(summary), end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
