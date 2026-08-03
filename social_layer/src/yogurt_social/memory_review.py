from __future__ import annotations

import argparse
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any, Optional

from .auto_memory import AutoMemoryStore


def _json_default(value: object) -> str:
    return str(value)


def _print_payload(payload: object, pretty: bool) -> None:
    print(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2 if pretty else None,
            default=_json_default,
        )
    )


def _revisions(db_path: Path, user_id: str, limit: int) -> list[dict[str, Any]]:
    with closing(sqlite3.connect(db_path)) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT id, kind, memory_key, previous_value, new_value,
                   action, candidate_id, created_at
            FROM memory_revisions
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (user_id, max(1, min(500, limit))),
        ).fetchall()
    return [dict(row) for row in rows]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inspect Yogurt V3 memory candidates without modifying them"
    )
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--pretty", action="store_true")
    subparsers = parser.add_subparsers(dest="command", required=True)

    candidates = subparsers.add_parser("candidates")
    candidates.add_argument("--user-id", required=True)
    candidates.add_argument(
        "--status",
        choices=("pending", "promoted", "merged", "conflict"),
    )

    snapshot = subparsers.add_parser("snapshot")
    snapshot.add_argument("--user-id", required=True)

    revisions = subparsers.add_parser("revisions")
    revisions.add_argument("--user-id", required=True)
    revisions.add_argument("--limit", type=int, default=100)
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    store = AutoMemoryStore(args.db)

    if args.command == "candidates":
        items = store.list_candidates(args.user_id, status=args.status)
        _print_payload(
            {
                "user_id": args.user_id,
                "status": args.status,
                "count": len(items),
                "items": items,
            },
            args.pretty,
        )
        return 0

    if args.command == "snapshot":
        _print_payload(store.base.snapshot(args.user_id), args.pretty)
        return 0

    if args.command == "revisions":
        items = _revisions(args.db.expanduser().resolve(), args.user_id, args.limit)
        _print_payload(
            {
                "user_id": args.user_id,
                "count": len(items),
                "items": items,
            },
            args.pretty,
        )
        return 0

    raise AssertionError("unreachable")


if __name__ == "__main__":
    raise SystemExit(main())
