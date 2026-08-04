from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Optional

from .memory_recall_gate import MemoryRecallGate


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inspect Yogurt V4.2 memory-recall gate decisions"
    )
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--pretty", action="store_true")
    subparsers = parser.add_subparsers(dest="command", required=True)

    status = subparsers.add_parser("status", help="List recall cooldown status")
    status.add_argument("--user-id", required=True)

    events = subparsers.add_parser("events", help="List recall decision audit events")
    events.add_argument("--user-id", required=True)
    events.add_argument("--conversation-key")
    events.add_argument("--limit", type=int, default=50)
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    gate = MemoryRecallGate(args.db)
    payload: dict[str, Any]
    if args.command == "status":
        items = gate.status(user_id=args.user_id)
        payload = {"count": len(items), "items": items}
    else:
        items = gate.events(
            user_id=args.user_id,
            conversation_key=args.conversation_key,
            limit=args.limit,
        )
        payload = {
            "user_id": args.user_id,
            "conversation_key": args.conversation_key,
            "count": len(items),
            "items": items,
        }
    print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
