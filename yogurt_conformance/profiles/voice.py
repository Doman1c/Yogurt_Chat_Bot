"""Reusable public Voice-facing sequences; no real TTS is involved."""

from yogurt_conformance.protocols.adapters import VoiceAdapterProtocol
from ._scenarios import assert_scenarios


def run_voice_conformance(adapter: VoiceAdapterProtocol) -> None:
    assert_scenarios(adapter, ("reject_draft", "dedupe_playback_intent", "partial_playback_extent", "interrupt_not_full", "zero_playback_not_exposure", "monotonic_progress"))
