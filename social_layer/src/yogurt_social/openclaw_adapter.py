from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

from .service import SocialLayerService

LOGGER = logging.getLogger("yogurt_social.openclaw_adapter")


@dataclass
class AdapterResult:
    """One-turn adapter output consumed by the OpenClaw plugin bridge."""

    original_message: str
    social_context: str
    final_model_input: Dict[str, str]
    metadata: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class OpenClawAdapter:
    """Prepare additive social context without rewriting the user's message.

    The adapter is deliberately model-free. It calls the deterministic V1
    social layer once, then returns a system-context contribution and the exact
    original user message as separate fields.
    """

    def __init__(self, service: SocialLayerService, enabled: bool = True) -> None:
        self.service = service
        self.enabled = enabled

    @classmethod
    def from_db(
        cls,
        db_path: Path,
        max_memories_per_reply: int = 3,
        enabled: bool = True,
    ) -> "OpenClawAdapter":
        return cls(
            SocialLayerService(
                db_path=db_path,
                max_memories_per_reply=max_memories_per_reply,
            ),
            enabled=enabled,
        )

    def prepare(self, user_id: str, message: str) -> AdapterResult:
        """Return a fail-open, one-turn injection plan.

        `message` is copied verbatim into both `original_message` and the
        `user_message` field of `final_model_input`. The OpenClaw plugin uses
        only `prepend_system_context` as its hook return value, so the active
        OpenClaw transcript and current prompt remain untouched.
        """

        original_message = message
        base_input = {
            "prepend_system_context": "",
            "user_message": original_message,
        }

        if not self.enabled:
            return AdapterResult(
                original_message=original_message,
                social_context="",
                final_model_input=base_input,
                metadata={
                    "enabled": False,
                    "applied": False,
                    "fail_open": True,
                    "reason": "disabled",
                    "original_message_preserved": True,
                },
            )

        try:
            if not isinstance(user_id, str) or not user_id.strip():
                raise ValueError("user_id must be a non-empty string")
            if not isinstance(message, str):
                raise TypeError("message must be a string")

            social_result = self.service.analyze(user_id.strip(), original_message)
            social_context = str(social_result.get("context_block") or "").strip()
            state = social_result.get("state") or {}
            memories = social_result.get("memories") or []
            final_model_input = {
                "prepend_system_context": social_context,
                "user_message": original_message,
            }
            return AdapterResult(
                original_message=original_message,
                social_context=social_context,
                final_model_input=final_model_input,
                metadata={
                    "enabled": True,
                    "applied": bool(social_context),
                    "fail_open": False,
                    "user_id": user_id.strip(),
                    "scene": state.get("scene"),
                    "mood": state.get("mood"),
                    "memory_count": len(memories),
                    "original_message_preserved": (
                        final_model_input["user_message"] == original_message
                    ),
                },
            )
        except Exception as exc:  # fail-open is an explicit V1 requirement
            LOGGER.exception("Yogurt social layer failed; continuing without injection")
            return AdapterResult(
                original_message=original_message,
                social_context="",
                final_model_input=base_input,
                metadata={
                    "enabled": True,
                    "applied": False,
                    "fail_open": True,
                    "reason": "social_layer_error",
                    "error_type": type(exc).__name__,
                    "original_message_preserved": True,
                },
            )


def default_db_path() -> Path:
    configured = os.environ.get("YOGURT_SOCIAL_DB")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".openclaw" / "yogurt-social" / "memory.sqlite3"


def _read_request(stream: Any) -> Mapping[str, Any]:
    raw = stream.read()
    if not raw:
        raise ValueError("expected one JSON request on stdin")
    request = json.loads(raw)
    if not isinstance(request, dict):
        raise ValueError("request must be a JSON object")
    return request


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Yogurt Social Layer V1 OpenClaw bridge"
    )
    parser.add_argument("--db", type=Path, default=default_db_path())
    parser.add_argument("--max-memories", type=int, default=3)
    parser.add_argument("--disabled", action="store_true")
    parser.add_argument("--pretty", action="store_true")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stderr,
        format="%(levelname)s %(name)s: %(message)s",
    )

    try:
        request = _read_request(sys.stdin)
        user_id = request.get("user_id")
        message = request.get("message")
        if not isinstance(user_id, str):
            raise ValueError("request.user_id must be a string")
        if not isinstance(message, str):
            raise ValueError("request.message must be a string")
    except Exception as exc:
        print(
            json.dumps(
                {
                    "error": "invalid_request",
                    "error_type": type(exc).__name__,
                },
                ensure_ascii=False,
            )
        )
        return 2

    try:
        adapter = OpenClawAdapter.from_db(
            db_path=args.db,
            max_memories_per_reply=max(0, args.max_memories),
            enabled=not args.disabled,
        )
        result = adapter.prepare(user_id=user_id, message=message).to_dict()
    except Exception as exc:
        LOGGER.exception(
            "Yogurt social layer initialization failed; continuing without injection"
        )
        result = AdapterResult(
            original_message=message,
            social_context="",
            final_model_input={
                "prepend_system_context": "",
                "user_message": message,
            },
            metadata={
                "enabled": not args.disabled,
                "applied": False,
                "fail_open": True,
                "reason": "initialization_error",
                "error_type": type(exc).__name__,
                "original_message_preserved": True,
            },
        ).to_dict()
    print(
        json.dumps(
            result,
            ensure_ascii=False,
            indent=2 if args.pretty else None,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
