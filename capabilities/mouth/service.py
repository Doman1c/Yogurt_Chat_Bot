from __future__ import annotations

import time
from typing import Protocol

from voice_orchestrator.client import SynthesizedAudio, VoiceRuntimeClient, VoiceRuntimeError
from voice_orchestrator.policy import VoicePlanError, plan_voice
from voice_orchestrator.schemas import VoicePlanInput
from voice_runtime.schemas import SpeechRequest

from .schemas import MouthErrorCode, SpeakRequest, SpeakResult


class _VoiceSynthesizer(Protocol):
    async def synthesize(self, request: SpeechRequest) -> SynthesizedAudio:
        """Synthesize through the already-bounded Voice Orchestrator client."""


def _error_code(error: Exception) -> MouthErrorCode:
    """Map private upstream failures to the small public Mouth error contract."""

    if not isinstance(error, VoiceRuntimeError):
        return "synthesis_failed"
    code = str(error)
    if "timeout" in code:
        return "timeout"
    if "unreachable" in code or "not_ready" in code:
        return "voice_runtime_unreachable"
    return "synthesis_failed"


class MouthService:
    """Facade from a final text request to Yogurt audio.

    The default client is the existing Voice Orchestrator client and therefore
    retains its loopback-only runtime boundary. No endpoint, model, reference,
    speed, or backend choice is accepted from a Mouth caller.

    Playback, streaming, interruption, and lip-sync are deliberately outside
    this first capability boundary.
    """

    def __init__(self, client: _VoiceSynthesizer | None = None) -> None:
        self._client = client or VoiceRuntimeClient()

    async def speak(self, request: SpeakRequest) -> SpeakResult:
        started_at = time.monotonic()
        try:
            planned = plan_voice(
                VoicePlanInput(
                    text=request.text,
                    language=request.language,
                    scene=request.scene,
                )
            )
        except (TypeError, ValueError, VoicePlanError):
            return SpeakResult(
                success=False,
                scene=request.scene,
                language=request.language,
                error_code="invalid_request",
            )

        try:
            synthesized = await self._client.synthesize(planned)
        except Exception as error:  # The public boundary must not expose upstream detail.
            return SpeakResult(
                success=False,
                scene=request.scene,
                language=request.language,
                error_code=_error_code(error),
            )

        return SpeakResult(
            success=True,
            audio=synthesized.audio,
            media_type=synthesized.media_type,
            scene=request.scene,
            voice="yogurt",
            language=synthesized.language,
            latency_ms=round((time.monotonic() - started_at) * 1000),
        )
