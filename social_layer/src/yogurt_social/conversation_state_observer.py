from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping, Optional

from .conversation_state_runtime import ConversationStateRuntimeStore
from .response_planner import ResponsePlanner


def _request(stream: Any) -> Mapping[str, Any]:
    raw = stream.read()
    if not raw:
        raise ValueError("expected one JSON request on stdin")
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("request must be a JSON object")
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Yogurt V4 conversation-state observer and V4.1 response planner"
    )
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--topic-ttl-minutes", type=int, default=60)
    parser.add_argument("--mood-ttl-minutes", type=int, default=30)
    parser.add_argument("--thread-ttl-minutes", type=int, default=1440)
    parser.add_argument("--max-open-threads", type=int, default=5)
    parser.add_argument("--max-recent-actions", type=int, default=6)
    parser.add_argument("--pretty", action="store_true")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        request = _request(sys.stdin)
        user_id = request.get("user_id")
        conversation_key = request.get("conversation_key")
        phase = request.get("phase")
        message = request.get("message", "")
        assistant_reply = request.get("assistant_reply", "")
        include_response_plan = request.get("include_response_plan") is True
        if not isinstance(user_id, str) or not user_id.strip():
            raise ValueError("user_id must be a non-empty string")
        if not isinstance(conversation_key, str) or not conversation_key.strip():
            raise ValueError("conversation_key must be a non-empty string")
        if phase not in {"user", "assistant"}:
            raise ValueError("phase must be user or assistant")
        if not isinstance(message, str) or not isinstance(assistant_reply, str):
            raise ValueError("message fields must be strings")
        if include_response_plan and phase != "user":
            raise ValueError("response plan is only available for user phase")

        store = ConversationStateRuntimeStore(
            args.db,
            topic_ttl_minutes=args.topic_ttl_minutes,
            mood_ttl_minutes=args.mood_ttl_minutes,
            thread_ttl_minutes=args.thread_ttl_minutes,
            max_open_threads=args.max_open_threads,
            max_recent_actions=args.max_recent_actions,
        )
        response_plan = None
        response_plan_context = ""
        if phase == "user":
            state = store.observe_user_turn(user_id, conversation_key, message)
            if include_response_plan:
                planner = ResponsePlanner()
                response_plan = planner.plan(message, state)
                response_plan_context = planner.build_context_block(response_plan)
        else:
            state = store.observe_assistant_turn(
                user_id,
                conversation_key,
                assistant_reply,
            )
        result = {
            "ok": True,
            "mode": "response_planner" if response_plan is not None else "observe_only",
            "phase": phase,
            "state": state.to_dict(),
            "response_plan": response_plan.to_dict() if response_plan is not None else None,
            "response_plan_context": response_plan_context,
        }
    except Exception as exc:
        result = {
            "ok": False,
            "mode": "observe_only",
            "error": "conversation_state_error",
            "error_type": type(exc).__name__,
        }
    print(json.dumps(result, ensure_ascii=False, indent=2 if args.pretty else None))
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
