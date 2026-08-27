"""Local microphone capture normalized to private mono 16 kHz PCM16 WAV."""

from __future__ import annotations

import os
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol


SAMPLE_RATE = 16000
CHANNELS = 1
SAMPLE_WIDTH_BYTES = 2


class AudioCaptureError(RuntimeError):
    pass


@dataclass(frozen=True)
class RecordedAudio:
    path: Path
    sample_rate: int = SAMPLE_RATE
    channels: int = CHANNELS
    sample_width_bytes: int = SAMPLE_WIDTH_BYTES


class MicrophoneBackend(Protocol):
    def start(self, directory: Path) -> Any:
        ...

    def stop(self, capture: Any) -> RecordedAudio:
        ...

    def cancel(self, capture: Any) -> None:
        ...


@dataclass
class _SoundDeviceCapture:
    directory: Path
    stream: Any
    chunks: bytearray = field(default_factory=bytearray)
    overflowed: bool = False


class SoundDeviceMicrophone:
    """Optional trusted Python microphone backend; imported only on CLI use."""

    def __init__(self, max_seconds: int = 120) -> None:
        if not 1 <= max_seconds <= 300:
            raise ValueError("invalid_capture_limit")
        self._max_bytes = SAMPLE_RATE * CHANNELS * SAMPLE_WIDTH_BYTES * max_seconds

    def start(self, directory: Path) -> _SoundDeviceCapture:
        try:
            import sounddevice  # type: ignore
        except ImportError as error:
            raise AudioCaptureError("microphone_backend_unavailable") from error
        if not directory.is_dir():
            raise AudioCaptureError("audio_tempdir_unavailable")
        capture: _SoundDeviceCapture

        def callback(indata, frames, time_info, status) -> None:
            del frames, time_info, status
            chunk = bytes(indata.tobytes())
            if len(capture.chunks) + len(chunk) > self._max_bytes:
                capture.overflowed = True
                return
            capture.chunks.extend(chunk)

        try:
            stream = sounddevice.InputStream(
                samplerate=SAMPLE_RATE, channels=CHANNELS, dtype="int16", callback=callback
            )
            capture = _SoundDeviceCapture(directory=directory, stream=stream)
            stream.start()
            return capture
        except Exception as error:
            raise AudioCaptureError("microphone_start_failed") from error

    def stop(self, capture: _SoundDeviceCapture) -> RecordedAudio:
        try:
            capture.stream.stop()
            capture.stream.close()
        except Exception as error:
            raise AudioCaptureError("microphone_stop_failed") from error
        if capture.overflowed:
            raise AudioCaptureError("audio_capture_too_long")
        if not capture.chunks:
            raise AudioCaptureError("empty_audio_capture")
        path = capture.directory / "input.wav"
        try:
            with wave.open(str(path), "wb") as output:
                output.setnchannels(CHANNELS)
                output.setsampwidth(SAMPLE_WIDTH_BYTES)
                output.setframerate(SAMPLE_RATE)
                output.writeframes(bytes(capture.chunks))
            os.chmod(str(path), 0o600)
        except OSError as error:
            raise AudioCaptureError("audio_write_failed") from error
        return RecordedAudio(path=path)

    def cancel(self, capture: _SoundDeviceCapture) -> None:
        try:
            capture.stream.abort()
            capture.stream.close()
        except Exception:
            # Cancellation is best-effort and must not leak backend detail.
            pass
