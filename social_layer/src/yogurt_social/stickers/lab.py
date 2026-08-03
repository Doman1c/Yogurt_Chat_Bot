from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from yogurt_social.router import StyleRouter

from .decision import StickerSendDecider
from .runtime import StickerRuntime


def _request() -> dict[str, Any]:
    payload = json.loads(sys.stdin.read() or "{}")
    if not isinstance(payload, dict):
        raise ValueError("request must be an object")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Yogurt Sticker V2.1 decision laboratory")
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--sticker-config", type=Path, required=True)
    args = parser.parse_args()
    try:
        request = _request()
        user_message = str(request.get("user_message") or "")
        assistant_reply = str(request.get("assistant_reply") or "")
        relationship = str(request.get("relationship") or "owner")
        explicit = request.get("explicit_request") is True
        forbidden = request.get("forbidden") is True
        if not user_message.strip():
            raise ValueError("user_message is required")

        state = StyleRouter().analyze(user_message, relationship=relationship)
        runtime = StickerRuntime.from_files(args.sticker_config, args.project_root)
        candidates = runtime.retrieve(
            message=user_message,
            assistant_reply=assistant_reply,
            scene=state.scene.value,
            mood=state.mood.value,
            relationship=state.relationship.value,
            allow_sticker=state.use_sticker or explicit,
            explicit_request=explicit,
        )
        decision = StickerSendDecider().decide(
            user_message=user_message,
            assistant_reply=assistant_reply,
            scene=state.scene.value,
            mood=state.mood.value,
            explicit_request=explicit,
            candidates=candidates,
            phase="post_reply",
            forbidden=forbidden,
        )
        print(
            json.dumps(
                {
                    "ok": True,
                    "state": state.to_dict(),
                    "decision": decision.to_dict(),
                    "candidates": [candidate.to_dict() for candidate in candidates],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    except Exception as exc:
        print(
            json.dumps(
                {"ok": False, "error": type(exc).__name__, "detail": str(exc)},
                ensure_ascii=False,
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
