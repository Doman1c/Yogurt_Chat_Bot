from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Optional

from .conversation_state import ConversationStateStore


def _print(payload: object, pretty: bool) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2 if pretty else None))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inspect Yogurt V4 conversation state without modifying it"
    )
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--pretty", action="store_true")
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list")
    list_parser.add_argument("--user-id")
    list_parser.add_argument("--limit", type=int, default=100)

    snapshot = subparsers.add_parser("snapshot")
    snapshot.add_argument("--user-id", required=True)
    snapshot.add_argument("--conversation-key", required=True)

    events = subparsers.add_parser("events")
    events.add_argument("--user-id", required=True)
    events.add_argument("--conversation-key", required=True)
    events.add_argument("--limit", type=int, default=100)
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    store = ConversationStateStore(args.db)

    if args.command == "list":
        items = store.list_states(args.user_id, limit=args.limit)
        _print({"count": len(items), "items": items}, args.pretty)
        return 0

    if args.command == "snapshot":
        state = store.get(args.user_id, args.conversation_key)
        _print(
            {
                "user_id": args.user_id,
                "conversation_key": args.conversation_key,
                "state": state.to_dict() if state is not None else None,
            },
            args.pretty,
        )
        return 0

    if args.command == "events":
        items = store.list_events(
            args.user_id,
            args.conversation_key,
            limit=args.limit,
        )
        _print(
            {
                "user_id": args.user_id,
                "conversation_key": args.conversation_key,
                "count": len(items),
                "items": items,
            },
            args.pretty,
        )
        return 0

    raise AssertionError("unreachable")


if __name__ == "__main__":
    raise SystemExit(main())
