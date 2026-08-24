from __future__ import annotations

import asyncio
import time
import uuid
import weakref
from dataclasses import dataclass, field
from typing import Optional

from capabilities.mouth.events import MouthEvent
from capabilities.mouth.playback import AudioArtifact, PlaybackController, PlaybackHandle, PlaybackState, PlaybackSink
from capabilities.mouth.schemas import SpeakRequest, SpeakResult
from capabilities.mouth.service import MouthService

from .routing import OutputRouter
from .schemas import (
    InterruptionPolicy,
    SpeechOutcome,
    SpeechPriority,
    UtteranceState,
    VoiceSessionErrorCode,
    VoiceSessionSnapshot,
    VoiceSessionState,
)


class _SynthesisCoordinator:
    """Serializes synthesis across VoiceSessions sharing one asyncio loop."""

    _locks: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Lock]" = weakref.WeakKeyDictionary()

    @classmethod
    def lock(cls) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        lock = cls._locks.get(loop)
        if lock is None:
            lock = asyncio.Lock()
            cls._locks[loop] = lock
        return lock


@dataclass
class _Job:
    utterance_id: str
    request: SpeakRequest
    priority: SpeechPriority
    sequence: int
    sink_id: str
    sink: PlaybackSink
    completion: "asyncio.Future[SpeechOutcome]"
    state: UtteranceState = UtteranceState.PENDING
    cancel_requested: asyncio.Event = field(default_factory=asyncio.Event)
    playback: Optional[PlaybackHandle] = None


class SpeechHandle:
    """Opaque channel-independent handle for one utterance."""

    def __init__(self, session: "VoiceSession", job: _Job) -> None:
        self.utterance_id = job.utterance_id
        self._session = session
        self._job = job

    @property
    def state(self) -> UtteranceState:
        return self._job.state

    def cancel(self) -> bool:
        if self._job.completion.done():
            return False
        asyncio.create_task(self._session.cancel(self.utterance_id))
        return True

    async def wait(self) -> SpeechOutcome:
        return await self._job.completion


class VoiceSession:
    """Bounded speech queue, trusted output routing, and playback interruption."""

    def __init__(
        self,
        mouth: MouthService,
        router: OutputRouter,
        *,
        session_id: str | None = None,
        max_queue_size: int = 16,
        interruption_policy: InterruptionPolicy = InterruptionPolicy.HIGHER_PRIORITY,
    ) -> None:
        if not (1 <= max_queue_size <= 32):
            raise ValueError("invalid_queue_size")
        self.session_id = session_id or uuid.uuid4().hex
        self._mouth = mouth
        self._router = router
        self._max_queue_size = max_queue_size
        self._interruption_policy = interruption_policy
        self._queue: list[_Job] = []
        self._current: _Job | None = None
        self._worker: asyncio.Task[None] | None = None
        self._lock: asyncio.Lock | None = None
        self._sequence = 0
        self._state = VoiceSessionState.IDLE
        self._last_error: VoiceSessionErrorCode | None = None
        self._events: list[MouthEvent] = []

    def _queue_lock(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    def snapshot(self) -> VoiceSessionSnapshot:
        return VoiceSessionSnapshot(
            session_id=self.session_id,
            state=self._state,
            queue_depth=len(self._queue),
            current_utterance_id=self._current.utterance_id if self._current else None,
            sink_id=self._current.sink_id if self._current else self._router.default_sink_id,
            last_error_code=self._last_error,
        )

    def events(self) -> tuple[MouthEvent, ...]:
        return tuple(self._events)

    async def submit(
        self,
        request: SpeakRequest,
        *,
        priority: SpeechPriority = SpeechPriority.NORMAL,
        sink_id: str | None = None,
    ) -> SpeechHandle:
        utterance_id = uuid.uuid4().hex
        completion: "asyncio.Future[SpeechOutcome]" = asyncio.get_running_loop().create_future()
        try:
            resolved_sink_id, sink = self._router.resolve(sink_id)
        except ValueError:
            job = _Job(utterance_id, request, priority, 0, sink_id or "invalid", _UnavailableSink(), completion)
            job.state = UtteranceState.FAILED
            self._complete(job, UtteranceState.FAILED, "unknown_sink")
            return SpeechHandle(self, job)

        async with self._queue_lock():
            self._sequence += 1
            job = _Job(
                utterance_id,
                request,
                priority,
                self._sequence,
                resolved_sink_id,
                sink,
                completion,
            )
            handle = SpeechHandle(self, job)
            if len(self._queue) >= self._max_queue_size:
                self._complete(job, UtteranceState.FAILED, "queue_full")
                return handle
            self._queue.append(job)
            self._queue.sort(key=lambda candidate: (-int(candidate.priority), candidate.sequence))
            self._emit(job, "queued")
            self._interrupt_current_for(job)
            if self._worker is None or self._worker.done():
                self._worker = asyncio.create_task(self._drain())
            return handle

    async def cancel(self, utterance_id: str) -> bool:
        async with self._queue_lock():
            for job in list(self._queue):
                if job.utterance_id == utterance_id:
                    self._queue.remove(job)
                    job.cancel_requested.set()
                    self._complete(job, UtteranceState.CANCELLED, "cancelled")
                    return True
            if self._current and self._current.utterance_id == utterance_id:
                self._current.cancel_requested.set()
                if self._current.playback:
                    self._current.playback.cancel()
                return True
        return False

    def _interrupt_current_for(self, incoming: _Job) -> None:
        current = self._current
        if current is None or current.completion.done():
            return
        should_interrupt = self._interruption_policy == InterruptionPolicy.ALWAYS or (
            self._interruption_policy == InterruptionPolicy.HIGHER_PRIORITY
            and incoming.priority > current.priority
        )
        if should_interrupt:
            current.cancel_requested.set()
            if current.playback:
                current.playback.cancel()

    async def _drain(self) -> None:
        while True:
            async with self._queue_lock():
                if not self._queue:
                    self._current = None
                    self._state = VoiceSessionState.IDLE
                    return
                job = self._queue.pop(0)
                self._current = job
            await self._run(job)
            async with self._queue_lock():
                if self._current is job:
                    self._current = None
                    self._state = VoiceSessionState.IDLE

    async def _run(self, job: _Job) -> None:
        if job.cancel_requested.is_set():
            self._complete(job, UtteranceState.CANCELLED, "cancelled")
            return
        job.state = UtteranceState.SYNTHESIZING
        self._state = VoiceSessionState.SYNTHESIZING
        self._emit(job, "speech_started")
        async with _SynthesisCoordinator.lock():
            result = await self._mouth.speak(job.request)
        if job.cancel_requested.is_set():
            self._complete(job, UtteranceState.CANCELLED, "cancelled")
            return
        if not result.success or not result.audio or not result.media_type:
            self._complete(job, UtteranceState.FAILED, _result_error(result))
            return
        job.state = UtteranceState.READY
        self._emit(job, "audio_ready")
        try:
            job.playback = PlaybackController().start(
                AudioArtifact(result.audio, result.media_type), job.sink
            )
        except Exception:
            self._complete(job, UtteranceState.FAILED, "playback_failed")
            return
        job.state = UtteranceState.PLAYING
        self._state = VoiceSessionState.PLAYING
        self._emit(job, "speech_started")
        playback_state = await job.playback.wait()
        if playback_state == PlaybackState.FINISHED:
            self._complete(job, UtteranceState.FINISHED)
        elif playback_state == PlaybackState.CANCELLED:
            self._complete(job, UtteranceState.CANCELLED, "cancelled")
        else:
            self._complete(job, UtteranceState.FAILED, "playback_failed")

    def _complete(
        self,
        job: _Job,
        state: UtteranceState,
        error_code: VoiceSessionErrorCode | None = None,
    ) -> None:
        job.state = state
        if error_code:
            self._last_error = error_code
        if state == UtteranceState.FINISHED:
            self._emit(job, "speech_finished")
        elif state == UtteranceState.CANCELLED:
            self._emit(job, "speech_cancelled", error_code)
        else:
            self._emit(job, "speech_failed", error_code)
        if not job.completion.done():
            job.completion.set_result(
                SpeechOutcome(utterance_id=job.utterance_id, state=state, error_code=error_code)
            )

    def _emit(
        self,
        job: _Job,
        kind: str,
        error_code: VoiceSessionErrorCode | None = None,
    ) -> None:
        self._events.append(
            MouthEvent(
                utterance_id=job.utterance_id,
                kind=kind,
                timestamp_ms=round(time.monotonic() * 1000),
                error_code=error_code,
            )
        )
        del self._events[:-64]


class _UnavailableSink:
    async def play(self, artifact: AudioArtifact, cancelled: asyncio.Event) -> None:
        raise RuntimeError("unavailable")


def _result_error(result: SpeakResult) -> VoiceSessionErrorCode:
    if result.error_code in {
        "voice_runtime_unreachable",
        "synthesis_timeout",
        "synthesis_failed",
        "invalid_request",
    }:
        return result.error_code
    return "synthesis_failed"
