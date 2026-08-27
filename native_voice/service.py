"""Half-duplex Native Voice turn lifecycle using existing Mouth/VoiceSession."""

from __future__ import annotations

import asyncio
import os
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, List, Optional, Protocol

from capabilities.mouth.schemas import SpeakRequest
from capabilities.voice_session import SpeechPriority
from capabilities.voice_session.schemas import UtteranceState

from .audio import AudioCaptureError, MicrophoneBackend, RecordedAudio
from .brain import BrainClient, BrainError, BrainRequest
from .memory import DisabledNativeMemory
from .schemas import (
    BrainReply,
    NativeVoiceEvent,
    NativeVoiceTurn,
    TranscriptResult,
    TurnPhase,
    TurnStatus,
)
from .stt import STTError, STTProvider


class NativeVoiceBusyError(RuntimeError):
    pass


class NativeVoiceSession(Protocol):
    async def submit(self, request: SpeakRequest, *, priority: SpeechPriority = SpeechPriority.NORMAL):
        ...


@dataclass
class _ActiveTurn:
    session_id: str
    turn_id: str
    tempdir: tempfile.TemporaryDirectory
    capture: Any
    started_at: float
    cancelled: bool = False
    task: Optional[asyncio.Task] = None
    speech_handle: Any = None
    transcript: Optional[TranscriptResult] = None
    reply: Optional[BrainReply] = None
    latencies_ms: Optional[dict] = None


class NativeVoiceService:
    """One local PTT turn at a time; no ambient capture and no Memory calls."""

    def __init__(
        self,
        microphone: MicrophoneBackend,
        stt: STTProvider,
        brain: BrainClient,
        voice_session: NativeVoiceSession,
        *,
        session_id: Optional[str] = None,
        memory: Optional[DisabledNativeMemory] = None,
        event_handler: Optional[Callable[[NativeVoiceEvent], None]] = None,
    ) -> None:
        self.session_id = session_id or uuid.uuid4().hex
        self._microphone = microphone
        self._stt = stt
        self._brain = brain
        self._voice_session = voice_session
        self._memory = memory or DisabledNativeMemory()
        if self._memory.mode != "off":
            raise ValueError("native_memory_not_available")
        self._event_handler = event_handler
        self._phase = TurnPhase.IDLE
        self._active: Optional[_ActiveTurn] = None
        self._events: List[NativeVoiceEvent] = []
        self._lock = asyncio.Lock()

    @property
    def phase(self) -> TurnPhase:
        return self._phase

    def events(self) -> tuple:
        return tuple(self._events)

    async def start_listening(self) -> NativeVoiceTurn:
        """Begin an explicit PTT capture. Speaking/thinking blocks re-entry."""
        async with self._lock:
            if self._active is not None or self._phase not in {
                TurnPhase.IDLE, TurnPhase.FINISHED, TurnPhase.CANCELLED, TurnPhase.FAILED,
            }:
                raise NativeVoiceBusyError("native_voice_busy")
            directory = tempfile.TemporaryDirectory(prefix="yogurt-native-voice-")
            path = Path(directory.name)
            try:
                os.chmod(str(path), 0o700)
                capture = self._microphone.start(path)
            except AudioCaptureError:
                directory.cleanup()
                raise
            except Exception as error:
                directory.cleanup()
                raise AudioCaptureError("microphone_start_failed") from error
            active = _ActiveTurn(
                session_id=self.session_id, turn_id=uuid.uuid4().hex,
                tempdir=directory, capture=capture, started_at=time.monotonic(), latencies_ms={},
            )
            self._active = active
            self._transition(active, TurnPhase.LISTENING, TurnStatus.ACTIVE)
            return self._turn(active, TurnPhase.LISTENING, TurnStatus.ACTIVE)

    async def stop_listening_and_process(self) -> NativeVoiceTurn:
        """Stop capture then complete STT -> Brain -> VoiceSession playback."""
        failure: Optional[str] = None
        active: Optional[_ActiveTurn] = None
        task: Optional[asyncio.Task] = None
        async with self._lock:
            active = self._active
            if active is None or self._phase != TurnPhase.LISTENING:
                raise NativeVoiceBusyError("native_voice_not_listening")
            try:
                audio = self._microphone.stop(active.capture)
            except AudioCaptureError as error:
                failure = str(error)
            else:
                self._transition(active, TurnPhase.TRANSCRIBING, TurnStatus.ACTIVE)
                active.task = asyncio.create_task(self._process(active, audio))
                task = active.task
        if failure is not None:
            return await self._terminal(active, TurnPhase.FAILED, TurnStatus.FAILED, failure)
        assert task is not None
        return await task

    async def cancel(self) -> bool:
        """Cancel capture, STT/Brain subprocess work, or current VoiceSession speech."""
        listening_active: Optional[_ActiveTurn] = None
        async with self._lock:
            active = self._active
            if active is None:
                return False
            active.cancelled = True
            phase = self._phase
            task = active.task
            handle = active.speech_handle
            if phase == TurnPhase.LISTENING:
                self._microphone.cancel(active.capture)
                listening_active = active
        if listening_active is not None:
            await self._terminal(listening_active, TurnPhase.CANCELLED, TurnStatus.CANCELLED, "cancelled")
            return True
        if handle is not None:
            handle.cancel()
        if task is not None and not task.done():
            task.cancel()
        return True

    async def _process(self, active: _ActiveTurn, audio: RecordedAudio) -> NativeVoiceTurn:
        try:
            transcript = await self._stt.transcribe(audio.path)
            active.transcript = transcript
            active.latencies_ms["capture_end_to_transcript"] = self._elapsed(active)
            self._ensure_active(active)
            self._transition(active, TurnPhase.THINKING, TurnStatus.ACTIVE)
            reply = await self._brain.reply(
                BrainRequest(
                    session_id=active.session_id, turn_id=active.turn_id,
                    text=transcript.text, language=transcript.language,
                )
            )
            active.reply = reply
            active.latencies_ms["transcript_to_brain_reply"] = self._elapsed(active) - active.latencies_ms[
                "capture_end_to_transcript"
            ]
            self._ensure_active(active)
            self._transition(active, TurnPhase.SPEAKING, TurnStatus.ACTIVE)
            # Casual is deterministic in A1; no additional planner/LLM invocation.
            handle = await self._voice_session.submit(
                SpeakRequest(text=reply.text, scene="casual", language=self._speech_language(transcript.language)),
                priority=SpeechPriority.NORMAL,
            )
            active.speech_handle = handle
            await self._capture_playback_start(active, handle)
            outcome = await handle.wait()
            if active.cancelled or outcome.state == UtteranceState.CANCELLED:
                return await self._terminal(active, TurnPhase.CANCELLED, TurnStatus.CANCELLED, "cancelled")
            if outcome.state != UtteranceState.FINISHED:
                return await self._terminal(active, TurnPhase.FAILED, TurnStatus.FAILED, "mouth_failed")
            return await self._terminal(active, TurnPhase.FINISHED, TurnStatus.FINISHED)
        except asyncio.CancelledError:
            if active.speech_handle is not None:
                active.speech_handle.cancel()
            return await self._terminal(active, TurnPhase.CANCELLED, TurnStatus.CANCELLED, "cancelled")
        except STTError as error:
            return await self._terminal(active, TurnPhase.FAILED, TurnStatus.FAILED, str(error))
        except BrainError as error:
            return await self._terminal(active, TurnPhase.FAILED, TurnStatus.FAILED, str(error))
        except (TypeError, ValueError):
            return await self._terminal(active, TurnPhase.FAILED, TurnStatus.FAILED, "mouth_failed")
        except Exception:
            return await self._terminal(active, TurnPhase.FAILED, TurnStatus.FAILED, "mouth_failed")
        finally:
            # Audio is deleted even when STT, Brain, or playback fails/cancels.
            if self._active is not active:
                active.tempdir.cleanup()

    async def _capture_playback_start(self, active: _ActiveTurn, handle: Any) -> None:
        # VoiceSession owns playback. Poll only its opaque state to measure start.
        for _ in range(500):
            if active.cancelled:
                return
            if getattr(handle, "state", None) == UtteranceState.PLAYING:
                active.latencies_ms["brain_reply_to_playback_started"] = self._elapsed(active) - sum(
                    active.latencies_ms.get(key, 0)
                    for key in ("capture_end_to_transcript", "transcript_to_brain_reply")
                )
                return
            if getattr(handle, "state", None) in {
                UtteranceState.FINISHED, UtteranceState.CANCELLED, UtteranceState.FAILED,
            }:
                return
            await asyncio.sleep(0.01)

    def _ensure_active(self, active: _ActiveTurn) -> None:
        if active.cancelled or self._active is not active:
            raise asyncio.CancelledError()

    async def _terminal(
        self, active: _ActiveTurn, phase: TurnPhase, status: TurnStatus, error_code: Optional[str] = None,
    ) -> NativeVoiceTurn:
        async with self._lock:
            if self._active is active:
                self._transition(active, phase, status, error_code)
                self._active = None
                active.tempdir.cleanup()
        return self._turn(active, phase, status, error_code)

    def _transition(
        self, active: _ActiveTurn, phase: TurnPhase, status: TurnStatus,
        error_code: Optional[str] = None,
    ) -> None:
        self._phase = phase
        event = NativeVoiceEvent(
            session_id=active.session_id, turn_id=active.turn_id, phase=phase, status=status,
            duration_ms=self._elapsed(active), error_code=error_code,
        )
        self._events.append(event)
        del self._events[:-64]
        if self._event_handler is not None:
            self._event_handler(event)

    def _turn(
        self, active: _ActiveTurn, phase: TurnPhase, status: TurnStatus,
        error_code: Optional[str] = None,
    ) -> NativeVoiceTurn:
        return NativeVoiceTurn(
            session_id=active.session_id, turn_id=active.turn_id, phase=phase, status=status,
            transcript=active.transcript, reply=active.reply, error_code=error_code,
            latencies_ms=dict(active.latencies_ms or {}),
        )

    @staticmethod
    def _speech_language(language: str) -> str:
        return language if language in {"zh", "ja", "en"} else "zh"

    @staticmethod
    def _elapsed(active: _ActiveTurn) -> int:
        return round((time.monotonic() - active.started_at) * 1000)
