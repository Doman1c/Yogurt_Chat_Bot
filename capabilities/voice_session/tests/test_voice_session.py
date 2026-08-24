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


def test_sink_failure_does_not_corrupt_the_next_job() -> None:
    class FailingOnceSink:
        def __init__(self) -> None:
            self.calls = 0

        async def play(self, artifact, cancelled) -> None:
            self.calls += 1
            if self.calls == 1:
                raise PlaybackError("playback_failed")

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
