from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .service import SocialLayerService


def _default_db() -> Path:
    configured = os.environ.get("YOGURT_SOCIAL_DB")
    if configured:
        return Path(configured).expanduser()
    return Path(__file__).resolve().parents[3] / "data" / "yogurt_social.sqlite3"


def _service(args: argparse.Namespace) -> SocialLayerService:
    return SocialLayerService(args.db, max_memories_per_reply=args.max_memories)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Yogurt Social Layer V1 CLI")
    parser.add_argument("--db", type=Path, default=_default_db())
    parser.add_argument("--max-memories", type=int, default=3)
    sub = parser.add_subparsers(dest="command", required=True)

    analyze = sub.add_parser("analyze", help="Analyze one incoming message")
    analyze.add_argument("--user-id", required=True)
    analyze.add_argument("--message", required=True)
    analyze.add_argument("--context-only", action="store_true")

    user = sub.add_parser("upsert-user", help="Create or update one user")
    user.add_argument("--user-id", required=True)
    user.add_argument("--display-name")
    user.add_argument("--relationship", default="owner", choices=("new", "familiar", "close", "owner"))

    fact = sub.add_parser("remember-fact", help="Add or update a stable fact")
    fact.add_argument("--user-id", required=True)
    fact.add_argument("--key", required=True)
    fact.add_argument("--value", required=True)

    preference = sub.add_parser("remember-preference", help="Add a user preference")
    preference.add_argument("--user-id", required=True)
    preference.add_argument("--category", required=True)
    preference.add_argument("--value", required=True)
    preference.add_argument("--dislike", action="store_true")

    episode = sub.add_parser("remember-episode", help="Add a shared episode")
    episode.add_argument("--user-id", required=True)
    episode.add_argument("--summary", required=True)
    episode.add_argument("--importance", type=float, default=0.5)
    episode.add_argument("--occurred-at")

    show = sub.add_parser("show-user", help="Show current stored memory")
    show.add_argument("--user-id", required=True)
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    service = _service(args)

    if args.command == "analyze":
        result = service.analyze(args.user_id, args.message)
        print(result["context_block"] if args.context_only else json.dumps(result, ensure_ascii=False, indent=2))
        return
    if args.command == "upsert-user":
        service.memory.upsert_user(args.user_id, args.display_name, args.relationship)
        print("ok")
        return
    if args.command == "remember-fact":
        service.memory.add_fact(args.user_id, args.key, args.value)
        print("ok")
        return
    if args.command == "remember-preference":
        service.memory.add_preference(
            args.user_id,
            args.category,
            args.value,
            polarity=-1 if args.dislike else 1,
        )
        print("ok")
        return
    if args.command == "remember-episode":
        service.memory.add_episode(
            args.user_id,
            args.summary,
            importance=args.importance,
            occurred_at=args.occurred_at,
        )
        print("ok")
        return
    if args.command == "show-user":
        print(json.dumps(service.memory.snapshot(args.user_id), ensure_ascii=False, indent=2))
        return

    parser.error(f"Unsupported command: {args.command}")


if __name__ == "__main__":
    main()
