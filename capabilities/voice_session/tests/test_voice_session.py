from __future__ import annotations

import asyncio

from capabilities.mouth.playback import MemoryPlaybackSink
from capabilities.mouth.errors import PlaybackError
from capabilities.mouth.schemas import SpeakRequest, SpeakResult
from capabilities.voice_session import (
    InterruptionPolicy,
    OutputRouter,
    SinkRegistry,
    SpeechPriority,
    VoiceSession,
)
from capabilities.voice_session.schemas import UtteranceState, VoiceSessionState


class FakeMouth:
    def __init__(self, *, failed_texts: set[str] | None = None) -> None:
        self.failed_texts = failed_texts or set()
        self.requests: list[str] = []
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.release.set()

    async def speak(self, request: SpeakRequest) -> SpeakResult:
        self.requests.append(request.text)
        self.started.set()
        await self.release.wait()
        if request.text in self.failed_texts:
            return SpeakResult(
                success=False,
                scene=request.scene,
                language=request.language,
                error_code="synthesis_failed",
            )
        return SpeakResult(
            success=True,
            audio=request.text.encode("utf-8"),
            media_type="audio/wav",
            scene=request.scene,
            language=request.language,
            latency_ms=1,
        )


def _event_kinds(session: VoiceSession, utterance_id: str) -> list[str]:
    return [event.kind for event in session.events() if event.utterance_id == utterance_id]


class BlockingSink:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.started_audio: list[bytes] = []
        self.starts: asyncio.Queue[bytes] = asyncio.Queue()
        self.active = 0
        self.max_active = 0

    async def play(self, artifact, cancelled, mark_started) -> None:
        self.started_audio.append(artifact.audio)
        await self.starts.put(artifact.audio)
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        self.started.set()
        mark_started()
        release_task = asyncio.create_task(self.release.wait())
        cancel_task = asyncio.create_task(cancelled.wait())
        done, pending = await asyncio.wait(
            {release_task, cancel_task}, return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        self.active -= 1
        if cancel_task in done:
            return


class CompletingMouth(FakeMouth):
    def __init__(self) -> None:
        super().__init__()
        self.completed = asyncio.Event()

    async def speak(self, request: SpeakRequest) -> SpeakResult:
        result = await super().speak(request)
        self.completed.set()
        return result


def _session(
    mouth: FakeMouth,
    sink: MemoryPlaybackSink,
    **kwargs,
) -> VoiceSession:
    return VoiceSession(
        mouth,
        OutputRouter(SinkRegistry({"memory": sink}), "memory"),
        session_id="voice-test",
        **kwargs,
    )


def test_fifo_and_priority_order_are_deterministic() -> None:
    async def run() -> None:
        mouth = FakeMouth()
        sink = MemoryPlaybackSink(block=True)
        session = _session(mouth, sink, interruption_policy=InterruptionPolicy.NEVER)
        first = await session.submit(SpeakRequest(text="first"), priority=SpeechPriority.NORMAL)
        await sink.started.wait()
        low = await session.submit(SpeakRequest(text="low"), priority=SpeechPriority.LOW)
        high = await session.submit(SpeakRequest(text="high"), priority=SpeechPriority.HIGH)
        normal = await session.submit(SpeakRequest(text="normal"), priority=SpeechPriority.NORMAL)
        sink.release.set()
        assert (await first.wait()).state == UtteranceState.FINISHED
        assert (await high.wait()).state == UtteranceState.FINISHED
        assert (await normal.wait()).state == UtteranceState.FINISHED
        assert (await low.wait()).state == UtteranceState.FINISHED
        assert [artifact.audio for artifact in sink.artifacts] == [b"first", b"high", b"normal", b"low"]

    asyncio.run(run())


def test_queue_bound_and_unknown_sink_are_rejected() -> None:
    async def run() -> None:
        mouth = FakeMouth()
        mouth.release.clear()
        session = _session(mouth, MemoryPlaybackSink(), max_queue_size=1)
        active = await session.submit(SpeakRequest(text="active"))
        await mouth.started.wait()
        queued = await session.submit(SpeakRequest(text="queued"))
        rejected = await session.submit(SpeakRequest(text="reject"))
        bad_sink = await session.submit(SpeakRequest(text="bad"), sink_id="http://bad")
        assert (await rejected.wait()).error_code == "queue_full"
        assert (await bad_sink.wait()).error_code == "unknown_sink"
        mouth.release.set()
        assert (await active.wait()).state == UtteranceState.FINISHED
        assert (await queued.wait()).state == UtteranceState.FINISHED

    asyncio.run(run())


def test_higher_priority_interrupts_playback_but_not_synthesis_claim() -> None:
    async def run() -> None:
        mouth = FakeMouth()
        sink = MemoryPlaybackSink(block=True)
        session = _session(mouth, sink, interruption_policy=InterruptionPolicy.HIGHER_PRIORITY)
        normal = await session.submit(SpeakRequest(text="normal"))
        await sink.started.wait()
        urgent = await session.submit(SpeakRequest(text="urgent"), priority=SpeechPriority.HIGH)
        assert (await normal.wait()).state == UtteranceState.CANCELLED
        sink.release.set()
        assert (await urgent.wait()).state == UtteranceState.FINISHED

    asyncio.run(run())


def test_cancel_pending_and_failure_do_not_deadlock_next_job() -> None:
    async def run() -> None:
        mouth = FakeMouth(failed_texts={"fail"})
        mouth.release.clear()
        sink = MemoryPlaybackSink()
        session = _session(mouth, sink)
        failing = await session.submit(SpeakRequest(text="fail"))
        await mouth.started.wait()
        pending = await session.submit(SpeakRequest(text="pending"))
        assert await session.cancel(pending.utterance_id) is True
        mouth.release.set()
        assert (await failing.wait()).state == UtteranceState.FAILED
        assert (await pending.wait()).state == UtteranceState.CANCELLED
        recovered = await session.submit(SpeakRequest(text="recovered"))
        assert (await recovered.wait()).state == UtteranceState.FINISHED
        assert session.snapshot().state == VoiceSessionState.IDLE

    asyncio.run(run())


def test_concurrent_submit_keeps_one_active_playback_and_safe_snapshot() -> None:
    async def run() -> None:
        mouth = FakeMouth()
        sink = MemoryPlaybackSink()
        session = _session(mouth, sink)
        handles = await asyncio.gather(
            *(session.submit(SpeakRequest(text=f"job-{index}")) for index in range(8))
        )
        outcomes = await asyncio.gather(*(handle.wait() for handle in handles))
        assert all(outcome.state == UtteranceState.FINISHED for outcome in outcomes)
        assert len(sink.artifacts) == 8
        snapshot = session.snapshot()
        assert snapshot.queue_depth == 0
        assert snapshot.current_utterance_id is None
        assert all("job-" not in event.model_dump_json() for event in session.events())

    asyncio.run(run())


def test_event_contract_has_one_ordered_lifecycle_per_utterance() -> None:
    async def run() -> None:
        session = _session(FakeMouth(), MemoryPlaybackSink())
        handle = await session.submit(SpeakRequest(text="ordered"))
        assert (await handle.wait()).state == UtteranceState.FINISHED
        assert _event_kinds(session, handle.utterance_id) == [
            "queued",
            "synthesis_started",
            "audio_ready",
            "playback_started",
            "speech_finished",
        ]

    asyncio.run(run())


def test_cancellation_and_failure_emit_one_terminal_event() -> None:
    async def run() -> None:
        cancelled_mouth = FakeMouth()
        cancelled_mouth.release.clear()
        cancelled_session = _session(cancelled_mouth, MemoryPlaybackSink())
        cancelled = await cancelled_session.submit(SpeakRequest(text="cancel"))
        await cancelled_mouth.started.wait()
        assert await cancelled_session.cancel(cancelled.utterance_id) is True
        cancelled_mouth.release.set()
        assert (await cancelled.wait()).state == UtteranceState.CANCELLED
        assert _event_kinds(cancelled_session, cancelled.utterance_id) == [
            "queued",
            "synthesis_started",
            "speech_cancelled",
        ]

        playback_sink = BlockingSink()
        playback_session = VoiceSession(
            FakeMouth(),
            OutputRouter(SinkRegistry({"local": playback_sink}), "local"),
            session_id="playback-cancel",
        )
        playback_cancelled = await playback_session.submit(SpeakRequest(text="playback-cancel"))
        await playback_sink.started.wait()
        assert await playback_session.cancel(playback_cancelled.utterance_id) is True
        assert (await playback_cancelled.wait()).state == UtteranceState.CANCELLED
        assert _event_kinds(playback_session, playback_cancelled.utterance_id) == [
            "queued",
            "synthesis_started",
            "audio_ready",
            "playback_started",
            "speech_cancelled",
        ]

        failed_session = _session(FakeMouth(failed_texts={"fail"}), MemoryPlaybackSink())
        failed = await failed_session.submit(SpeakRequest(text="fail"))
        assert (await failed.wait()).state == UtteranceState.FAILED
        assert _event_kinds(failed_session, failed.utterance_id) == [
            "queued",
            "synthesis_started",
            "speech_failed",
        ]

    asyncio.run(run())


def test_registered_sink_serializes_two_sessions_and_allows_distinct_sinks() -> None:
    async def run() -> None:
        shared_sink = BlockingSink()
        registry = SinkRegistry({"shared": shared_sink})
        first = VoiceSession(FakeMouth(), OutputRouter(registry, "shared"), session_id="first")
        second_mouth = CompletingMouth()
        second = VoiceSession(second_mouth, OutputRouter(registry, "shared"), session_id="second")
        first_handle = await first.submit(SpeakRequest(text="first"))
        assert await shared_sink.starts.get() == b"first"
        second_handle = await second.submit(SpeakRequest(text="second"))
        await second_mouth.completed.wait()
        shared_sink.release.set()
        assert (await first_handle.wait()).state == UtteranceState.FINISHED
        assert await shared_sink.starts.get() == b"second"
        assert (await second_handle.wait()).state == UtteranceState.FINISHED
        assert shared_sink.started_audio == [b"first", b"second"]
        assert shared_sink.max_active == 1

        cancellation_sink = BlockingSink()
        cancellation_registry = SinkRegistry({"local": cancellation_sink})
        active = VoiceSession(
            FakeMouth(), OutputRouter(cancellation_registry, "local"), session_id="active"
        )
        waiting_mouth = CompletingMouth()
        waiting = VoiceSession(
            waiting_mouth, OutputRouter(cancellation_registry, "local"), session_id="waiting"
        )
        active_handle = await active.submit(SpeakRequest(text="active"))
        assert await cancellation_sink.starts.get() == b"active"
        waiting_handle = await waiting.submit(SpeakRequest(text="waiting"))
        await waiting_mouth.completed.wait()
        assert await waiting.cancel(waiting_handle.utterance_id) is True
        cancellation_sink.release.set()
        assert (await active_handle.wait()).state == UtteranceState.FINISHED
        assert (await waiting_handle.wait()).state == UtteranceState.CANCELLED
        assert cancellation_sink.started_audio == [b"active"]

        left_sink = BlockingSink()
        right_sink = BlockingSink()
        split_registry = SinkRegistry({"left": left_sink, "right": right_sink})
        left = VoiceSession(FakeMouth(), OutputRouter(split_registry, "left"), session_id="left")
        right = VoiceSession(FakeMouth(), OutputRouter(split_registry, "right"), session_id="right")
        left_handle = await left.submit(SpeakRequest(text="left"))
        right_handle = await right.submit(SpeakRequest(text="right"))
        await left_sink.started.wait()
        await right_sink.started.wait()
        assert left_sink.active == right_sink.active == 1
        left_sink.release.set()
        right_sink.release.set()
        assert (await left_handle.wait()).state == UtteranceState.FINISHED
        assert (await right_handle.wait()).state == UtteranceState.FINISHED

    asyncio.run(run())


def test_sink_failure_does_not_corrupt_the_next_job() -> None:
    class FailingOnceSink:
        def __init__(self) -> None:
            self.calls = 0

        async def play(self, artifact, cancelled, mark_started) -> None:
            self.calls += 1
            if self.calls == 1:
                raise PlaybackError("playback_failed")
            mark_started()

    async def run() -> None:
        mouth = FakeMouth()
        sink = FailingOnceSink()
        session = VoiceSession(
            mouth,
            OutputRouter(SinkRegistry({"local": sink}), "local"),
            session_id="sink-failure",
        )
        failed = await session.submit(SpeakRequest(text="bad"))
        recovered = await session.submit(SpeakRequest(text="good"))
        assert (await failed.wait()).state == UtteranceState.FAILED
        assert (await recovered.wait()).state == UtteranceState.FINISHED
        assert session.snapshot().last_error_code == "playback_failed"

    asyncio.run(run())
