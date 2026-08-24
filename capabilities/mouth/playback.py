from __future__ import annotations

import asyncio
import os
import tempfile
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Protocol

from .errors import PlaybackError


MAX_AUDIO_BYTES = 20 * 1024 * 1024


@dataclass(frozen=True)
class AudioArtifact:
    """In-memory audio passed from synthesis to a trusted output sink."""

    audio: bytes
    media_type: str

    def validate(self) -> None:
        if self.media_type.lower() != "audio/wav":
            raise PlaybackError("unsupported_media_type")
        if not self.audio:
            raise PlaybackError("empty_audio")
        if len(self.audio) > MAX_AUDIO_BYTES:
            raise PlaybackError("audio_too_large")


class PlaybackState(str, Enum):
    PENDING = "pending"
    PLAYING = "playing"
    FINISHED = "finished"
    CANCELLED = "cancelled"
    FAILED = "failed"


class PlaybackSink(Protocol):
    """Trusted local output target. Callers select only registered sink IDs."""

    async def play(self, artifact: AudioArtifact, cancelled: asyncio.Event) -> None:
        """Play an artifact or return after cancellation."""


class PlaybackHandle:
    def __init__(self) -> None:
        self._cancelled = asyncio.Event()
        self._done = asyncio.Event()
        self._state = PlaybackState.PENDING
        self._error_code: str | None = None

    @property
    def state(self) -> PlaybackState:
        return self._state

    @property
    def error_code(self) -> str | None:
        return self._error_code

    def cancel(self) -> bool:
        if self._done.is_set():
            return False
        self._cancelled.set()
        return True

    async def wait(self) -> PlaybackState:
        await self._done.wait()
        return self._state

    def _set_state(self, state: PlaybackState, error_code: str | None = None) -> None:
        self._state = state
        self._error_code = error_code
        if state in {PlaybackState.FINISHED, PlaybackState.CANCELLED, PlaybackState.FAILED}:
            self._done.set()


class PlaybackController:
    """Coordinates one local playback operation without owning synthesis."""

    def start(self, artifact: AudioArtifact, sink: PlaybackSink) -> PlaybackHandle:
        artifact.validate()
        handle = PlaybackHandle()
        asyncio.create_task(self._run(artifact, sink, handle))
        return handle

    async def _run(
        self,
        artifact: AudioArtifact,
        sink: PlaybackSink,
        handle: PlaybackHandle,
    ) -> None:
        if handle._cancelled.is_set():
            handle._set_state(PlaybackState.CANCELLED)
            return
        handle._set_state(PlaybackState.PLAYING)
        try:
            await sink.play(artifact, handle._cancelled)
        except PlaybackError as error:
            handle._set_state(PlaybackState.FAILED, str(error))
        except Exception:
            handle._set_state(PlaybackState.FAILED, "playback_failed")
        else:
            handle._set_state(
                PlaybackState.CANCELLED if handle._cancelled.is_set() else PlaybackState.FINISHED
            )


class MemoryPlaybackSink:
    """Deterministic trusted sink for tests and buffer-only callers."""

    def __init__(self, *, block: bool = False) -> None:
        self.artifacts: list[AudioArtifact] = []
        self._block = block
        self._started: asyncio.Event | None = None
        self._release: asyncio.Event | None = None

    def _events(self) -> tuple[asyncio.Event, asyncio.Event]:
        if self._started is None or self._release is None:
            self._started = asyncio.Event()
            self._release = asyncio.Event()
            if not self._block:
                self._release.set()
        return self._started, self._release

    @property
    def started(self) -> asyncio.Event:
        return self._events()[0]

    @property
    def release(self) -> asyncio.Event:
        return self._events()[1]

    async def play(self, artifact: AudioArtifact, cancelled: asyncio.Event) -> None:
        started, release = self._events()
        self.artifacts.append(artifact)
        started.set()
        release_task = asyncio.create_task(release.wait())
        cancel_task = asyncio.create_task(cancelled.wait())
        done, pending = await asyncio.wait(
            {release_task, cancel_task}, return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        if cancel_task in done:
            return


class MacOSPlaybackSink:
    """macOS-only trusted sink using the fixed system afplay executable."""

    EXECUTABLE = "/usr/bin/afplay"

    async def play(self, artifact: AudioArtifact, cancelled: asyncio.Event) -> None:
        artifact.validate()
        if not os.path.isfile(self.EXECUTABLE):
            raise PlaybackError("playback_unavailable")

        path: Path | None = None
        process: asyncio.subprocess.Process | None = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as temporary:
                temporary.write(artifact.audio)
                path = Path(temporary.name)
            try:
                process = await asyncio.create_subprocess_exec(
                    self.EXECUTABLE,
                    str(path),
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
            except OSError as error:
                raise PlaybackError("playback_unavailable") from error

            process_task = asyncio.create_task(process.wait())
            cancel_task = asyncio.create_task(cancelled.wait())
            done, pending = await asyncio.wait(
                {process_task, cancel_task}, return_when=asyncio.FIRST_COMPLETED
            )
            if cancel_task in done and process.returncode is None:
                process.terminate()
                await process.wait()
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            if not cancelled.is_set() and process.returncode != 0:
                raise PlaybackError("playback_failed")
        finally:
            if path is not None:
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
