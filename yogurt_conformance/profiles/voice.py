"""Behavioral Voice-facing sequences; no real TTS is involved."""

from yogurt_contracts.foundation.identifiers import SegmentId
from yogurt_conformance.protocols.adapters import VoiceAdapterProtocol


def run_voice_conformance(adapter: VoiceAdapterProtocol) -> None:
    draft = adapter.submit_segment(SegmentId(value="draft"), committed=False)
    assert draft.intent_count == 0
    segment = SegmentId(value="committed")
    started = adapter.submit_segment(segment, committed=True)
    assert started.intent_count == 1 and adapter.retry_playback(segment).intent_count == 1
    progressed = adapter.report_progress(segment, 1000, 10)
    stale = adapter.report_progress(segment, 200, 3)
    assert progressed.extent is not None and stale.extent is not None
    assert stale.extent.played_duration_ms == 1000 and stale.extent.end_text_offset == 10
    interrupted = adapter.interrupt(segment)
    assert interrupted.interrupted and not interrupted.completed
    cancelled = adapter.cancel(segment)
    assert cancelled.cancelled and not cancelled.completed
