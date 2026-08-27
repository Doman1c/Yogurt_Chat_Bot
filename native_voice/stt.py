"""Bounded local-only STT providers for Native Voice."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Protocol, Sequence

from .config import STTConfig
from .schemas import TranscriptResult, resolve_transcript_language


class STTError(RuntimeError):
    """Bounded STT failure; never includes executable, model, or stderr details."""


class STTProvider(Protocol):
    async def transcribe(self, audio_path: Path) -> TranscriptResult:
        ...


class _OutputLimitExceeded(RuntimeError):
    pass


async def _read_limited(reader: asyncio.StreamReader, maximum: int) -> bytes:
    result = bytearray()
    while True:
        chunk = await reader.read(4096)
        if not chunk:
            return bytes(result)
        result.extend(chunk)
        if len(result) > maximum:
            raise _OutputLimitExceeded()


async def _terminate(process: asyncio.subprocess.Process) -> None:
    if process.returncode is None:
        process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=2.0)
    except asyncio.TimeoutError:
        if process.returncode is None:
            process.kill()
        await process.wait()


class WhisperCppSTT:
    """whisper.cpp adapter with fixed argv and no automatic downloads."""

    def __init__(self, config: STTConfig) -> None:
        self._config = config

    def _argv(self, audio_path: Path) -> Sequence[str]:
        # The executable/model originate only from trusted local configuration.
        argv = [str(self._config.executable), "-m", str(self._config.model), "-f", str(audio_path), "-nt"]
        if self._config.language != "auto":
            argv.extend(["-l", self._config.language])
        return tuple(argv)

    async def transcribe(self, audio_path: Path) -> TranscriptResult:
        if not audio_path.is_file():
            raise STTError("audio_not_available")
        started = time.monotonic()
        try:
            process = await asyncio.create_subprocess_exec(
                *self._argv(audio_path),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
        except OSError as error:
            raise STTError("stt_unavailable") from error
        assert process.stdout is not None
        output_task = asyncio.create_task(_read_limited(process.stdout, self._config.max_stdout_bytes))
        process_task = asyncio.create_task(process.wait())
        try:
            done, pending = await asyncio.wait(
                {output_task, process_task},
                timeout=self._config.timeout_seconds,
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
                raise STTError("stt_failed")
        except asyncio.CancelledError:
            await _terminate(process)
            output_task.cancel()
            await asyncio.gather(output_task, return_exceptions=True)
            raise
        except _OutputLimitExceeded as error:
            await _terminate(process)
            raise STTError("stt_output_too_large") from error
        except asyncio.TimeoutError as error:
            await _terminate(process)
            raise STTError("stt_timeout") from error
        except STTError:
            await _terminate(process)
            raise
        except Exception as error:
            await _terminate(process)
            raise STTError("stt_failed") from error
        finally:
            for task in (output_task, process_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(output_task, process_task, return_exceptions=True)
        try:
            text = output.decode("utf-8", errors="replace")
            return TranscriptResult(
                text=text, language=resolve_transcript_language(text, self._config.language),
                duration_ms=round((time.monotonic() - started) * 1000),
            )
        except ValueError as error:
            raise STTError("empty_transcript") from error
