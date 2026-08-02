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
from .stickers.runtime import StickerRuntime

LOGGER = logging.getLogger("yogurt_social.openclaw_adapter")


@dataclass
class AdapterResult:
    """One-turn adapter output consumed by the OpenClaw plugin bridge."""

    original_message: str
    social_context: str
    sticker_candidates: list[Dict[str, Any]]
    final_model_input: Dict[str, str]
    metadata: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class OpenClawAdapter:
    """Prepare additive social and sticker context without rewriting user text."""

    def __init__(
        self,
        service: SocialLayerService,
        enabled: bool = True,
        sticker_runtime: StickerRuntime | None = None,
        sticker_runtime_error: str | None = None,
    ) -> None:
        self.service = service
        self.enabled = enabled
        self.sticker_runtime = sticker_runtime
        self.sticker_runtime_error = sticker_runtime_error

    @classmethod
    def from_db(
        cls,
        db_path: Path,
        max_memories_per_reply: int = 3,
        enabled: bool = True,
        sticker_config_path: Path | None = None,
        project_root: Path | None = None,
    ) -> "OpenClawAdapter":
        sticker_runtime: StickerRuntime | None = None
        sticker_runtime_error: str | None = None
        if sticker_config_path is not None:
            try:
                resolved_root = (project_root or Path.cwd()).expanduser().resolve()
                resolved_config = sticker_config_path.expanduser()
                if not resolved_config.is_absolute():
                    resolved_config = resolved_root / resolved_config
                sticker_runtime = StickerRuntime.from_files(
                    resolved_config.resolve(),
                    resolved_root,
                )
            except Exception as exc:
                # Sticker failures must never disable the established V1 social layer.
                sticker_runtime_error = type(exc).__name__
                LOGGER.warning(
                    "Yogurt sticker runtime unavailable; continuing without candidates"
                )

        return cls(
            SocialLayerService(
                db_path=db_path,
                max_memories_per_reply=max_memories_per_reply,
            ),
            enabled=enabled,
            sticker_runtime=sticker_runtime,
            sticker_runtime_error=sticker_runtime_error,
        )

    @staticmethod
    def _build_sticker_context(candidates: list[Dict[str, Any]]) -> str:
        if not candidates:
            return ""
        lines = [
            "[Yogurt Sticker Candidates]",
            "本轮可以不使用表情包；确实适合时最多选择一张。",
            "需要使用时只能调用 choose_sticker，并传入下面列出的 sticker_id。",
            "不要在回复文本中输出 sticker_id、文件名或本地路径。",
        ]
        for candidate in candidates:
            emotions = ", ".join(candidate.get("emotions") or []) or "neutral"
            tones = ", ".join(candidate.get("tones") or []) or "neutral"
            lines.extend(
                [
                    f"- sticker_id: {candidate['id']}",
                    f"  description: {candidate['description']}",
                    f"  emotions: {emotions}",
                    f"  tones: {tones}",
                ]
            )
        return "\n".join(lines)

    def prepare(
        self,
        user_id: str,
        message: str,
        explicit_sticker_request: bool = False,
    ) -> AdapterResult:
        """Return a fail-open, one-turn injection plan."""

        original_message = message
        base_input = {
            "prepend_system_context": "",
            "user_message": original_message,
        }

        if not self.enabled:
            return AdapterResult(
                original_message=original_message,
                social_context="",
                sticker_candidates=[],
                final_model_input=base_input,
                metadata={
                    "enabled": False,
                    "applied": False,
                    "fail_open": True,
                    "reason": "disabled",
                    "original_message_preserved": True,
                    "sticker_candidate_count": 0,
                    "explicit_sticker_request": explicit_sticker_request,
                },
            )

        try:
            if not isinstance(user_id, str) or not user_id.strip():
                raise ValueError("user_id must be a non-empty string")
            if not isinstance(message, str):
                raise TypeError("message must be a string")

            social_result = self.service.analyze(user_id.strip(), original_message)
            state = social_result.get("state") or {}
            memories = social_result.get("memories") or []
            base_social_context = str(social_result.get("context_block") or "").strip()

            candidates: list[Dict[str, Any]] = []
            sticker_error = self.sticker_runtime_error
            if self.sticker_runtime is not None:
                try:
                    candidates = [
                        candidate.to_dict()
                        for candidate in self.sticker_runtime.retrieve(
                            message=original_message,
                            scene=str(state.get("scene") or "casual"),
                            mood=str(state.get("mood") or "neutral"),
                            relationship=str(state.get("relationship") or "owner"),
                            allow_sticker=(
                                bool(state.get("use_sticker", False))
                                or explicit_sticker_request
                            ),
                            explicit_request=explicit_sticker_request,
                        )
                    ]
                except Exception as exc:
                    sticker_error = type(exc).__name__
                    LOGGER.warning(
                        "Yogurt sticker retrieval failed; continuing without candidates"
                    )
                    candidates = []

            sticker_context = self._build_sticker_context(candidates)
            context_parts = [part for part in (base_social_context, sticker_context) if part]
            social_context = "\n\n".join(context_parts)
            final_model_input = {
                "prepend_system_context": social_context,
                "user_message": original_message,
            }
            metadata: Dict[str, Any] = {
                "enabled": True,
                "applied": bool(social_context),
                "fail_open": False,
                "user_id": user_id.strip(),
                "scene": state.get("scene"),
                "mood": state.get("mood"),
                "memory_count": len(memories),
                "sticker_candidate_count": len(candidates),
                "explicit_sticker_request": explicit_sticker_request,
                "original_message_preserved": (
                    final_model_input["user_message"] == original_message
                ),
            }
            if sticker_error:
                metadata["sticker_fail_open"] = True
                metadata["sticker_error_type"] = sticker_error

            return AdapterResult(
                original_message=original_message,
                social_context=social_context,
                sticker_candidates=candidates,
                final_model_input=final_model_input,
                metadata=metadata,
            )
        except Exception as exc:  # fail-open is an explicit V1 requirement
            LOGGER.exception("Yogurt social layer failed; continuing without injection")
            return AdapterResult(
                original_message=original_message,
                social_context="",
                sticker_candidates=[],
                final_model_input=base_input,
                metadata={
                    "enabled": True,
                    "applied": False,
                    "fail_open": True,
                    "reason": "social_layer_error",
                    "error_type": type(exc).__name__,
                    "original_message_preserved": True,
                    "sticker_candidate_count": 0,
                    "explicit_sticker_request": explicit_sticker_request,
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
        description="Yogurt Social Layer V1 + Sticker V2 OpenClaw bridge"
    )
    parser.add_argument("--db", type=Path, default=default_db_path())
    parser.add_argument("--max-memories", type=int, default=3)
    parser.add_argument("--sticker-config", type=Path)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
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
        explicit_sticker_request = request.get("explicit_sticker_request") is True
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
            sticker_config_path=args.sticker_config,
            project_root=args.project_root,
        )
        result = adapter.prepare(
            user_id=user_id,
            message=message,
            explicit_sticker_request=explicit_sticker_request,
        ).to_dict()
    except Exception as exc:
        LOGGER.exception(
            "Yogurt social layer initialization failed; continuing without injection"
        )
        result = AdapterResult(
            original_message=message,
            social_context="",
            sticker_candidates=[],
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
                "sticker_candidate_count": 0,
                "explicit_sticker_request": explicit_sticker_request,
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
