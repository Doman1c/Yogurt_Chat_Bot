"""Native Voice access to the existing local OpenClaw/Yogurt Brain."""

from __future__ import annotations

import asyncio
import json
import math
import time
from dataclasses import dataclass
from typing import Any, Optional, Protocol, Sequence

from .config import BrainConfig
from .schemas import BrainReply, native_conversation_key
from .stt import _OutputLimitExceeded, _read_limited, _terminate


class BrainError(RuntimeError):
    """Bounded brain error that cannot leak prompts, paths, or subprocess stderr."""


@dataclass(frozen=True)
class BrainRequest:
    session_id: str
    turn_id: str
    text: str
    language: str

    def __post_init__(self) -> None:
        if not self.turn_id or len(self.turn_id) > 64:
            raise ValueError("invalid_turn_id")
        if not self.text or len(self.text) > 4000:
            raise ValueError("invalid_brain_input")


class BrainClient(Protocol):
    async def reply(self, request: BrainRequest) -> BrainReply:
        ...


def _final_text(payload: Any) -> Optional[str]:
    """Extract only named final-text fields, never reasoning/tool trace fields."""
    if isinstance(payload, str):
        return payload
    if not isinstance(payload, dict):
        return None
    for key in ("reply", "final", "text"):
        value = payload.get(key)
        if isinstance(value, str):
            return value
    message = payload.get("message")
    if isinstance(message, str):
        return message
    if isinstance(message, dict):
        for key in ("text", "content"):
            value = message.get(key)
            if isinstance(value, str):
                return value
    result = payload.get("result")
    if isinstance(result, dict):
        return _final_text(result)
    return None


class OpenClawAgentBrain:
    """Calls the configured Yogurt agent without a channel or delivery flag."""

    def __init__(self, config: BrainConfig, model_id: Optional[str] = None) -> None:
        self._config = config
        self._model_id = model_id

    def _argv(self, request: BrainRequest) -> Sequence[str]:
        # Do not add --deliver, --channel, --reply-channel, or --reply-to.
        return (
            str(self._config.executable), "agent", "--agent", self._config.agent_id,
            "--session-key", native_conversation_key(request.session_id),
            "--message", request.text, "--json", "--timeout",
            str(int(math.ceil(self._config.timeout_seconds))),
        )

    async def reply(self, request: BrainRequest) -> BrainReply:
        started = time.monotonic()
        try:
            process = await asyncio.create_subprocess_exec(
                *self._argv(request),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
        except OSError as error:
            raise BrainError("brain_unavailable") from error
        assert process.stdout is not None
        output_task = asyncio.create_task(_read_limited(process.stdout, self._config.max_stdout_bytes))
        process_task = asyncio.create_task(process.wait())
        try:
            done, pending = await asyncio.wait(
                {output_task, process_task}, timeout=self._config.timeout_seconds,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if not done:
                raise asyncio.TimeoutError()
            if output_task in done and output_task.exception() is not None:
                raise output_task.exception()  # type: ignore[misc]
            if process_task not in done:
                await asyncio.wait_for(process_task, timeout=self._config.timeout_seconds)
            output = await output_task
            if process.returncode != 0:
                raise BrainError("brain_failed")
        except asyncio.CancelledError:
            await _terminate(process)
            output_task.cancel()
            await asyncio.gather(output_task, return_exceptions=True)
            raise
        except _OutputLimitExceeded as error:
            await _terminate(process)
            raise BrainError("brain_output_too_large") from error
        except asyncio.TimeoutError as error:
            await _terminate(process)
            raise BrainError("brain_timeout") from error
        except BrainError:
            await _terminate(process)
            raise
        except Exception as error:
            await _terminate(process)
            raise BrainError("brain_failed") from error
        finally:
            for task in (output_task, process_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(output_task, process_task, return_exceptions=True)
        try:
            payload = json.loads(output.decode("utf-8"))
            text = _final_text(payload)
            if text is None:
                raise ValueError("missing_final")
            reply = BrainReply(text=text, model_id=self._model_id)
        except (UnicodeDecodeError, ValueError, TypeError, json.JSONDecodeError) as error:
            raise BrainError("brain_invalid_response") from error
        del started  # Timing is emitted by NativeVoiceService, not persisted here.
        return reply
