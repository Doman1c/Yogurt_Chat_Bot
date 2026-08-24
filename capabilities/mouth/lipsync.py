from __future__ import annotations

import io
import math
import struct
import wave
from pydantic import BaseModel, Field

from .errors import LipSyncError


class MouthFrame(BaseModel):
    """Amplitude-only lipsync frame; it is not a phoneme or viseme."""

    timestamp_ms: int = Field(ge=0)
    amplitude: float = Field(ge=0.0, le=1.0)


def _rms_from_pcm(raw: bytes, sample_width: int) -> float:
    total = 0.0
    count = 0
    if sample_width == 1:
        for value in raw:
            sample = (value - 128) / 128.0
            total += sample * sample
            count += 1
    elif sample_width == 2:
        for (value,) in struct.iter_unpack("<h", raw):
            sample = value / 32768.0
            total += sample * sample
            count += 1
    elif sample_width == 3:
        for index in range(0, len(raw) - 2, 3):
            value = int.from_bytes(raw[index : index + 3], "little", signed=True)
            sample = value / 8388608.0
            total += sample * sample
            count += 1
    elif sample_width == 4:
        for (value,) in struct.iter_unpack("<i", raw):
            sample = value / 2147483648.0
            total += sample * sample
            count += 1
    else:
        raise LipSyncError("unsupported_wav_format")
    if count == 0:
        raise LipSyncError("invalid_wav")
    return math.sqrt(total / count)


def amplitude_envelope(
    audio: bytes,
    media_type: str,
    *,
    frame_rate: int = 30,
) -> list[MouthFrame]:
    """Read a deterministic RMS amplitude envelope from an in-memory PCM WAV."""

    if media_type.lower() != "audio/wav":
        raise LipSyncError("unsupported_media_type")
    if not audio or len(audio) > 20 * 1024 * 1024:
        raise LipSyncError("invalid_wav")
    if not (1 <= frame_rate <= 60):
        raise LipSyncError("invalid_frame_rate")
    try:
        with wave.open(io.BytesIO(audio), "rb") as source:
            channels = source.getnchannels()
            sample_width = source.getsampwidth()
            sample_rate = source.getframerate()
            if source.getcomptype() != "NONE" or not (1 <= channels <= 8):
                raise LipSyncError("unsupported_wav_format")
            if not (1 <= sample_rate <= 192000):
                raise LipSyncError("invalid_wav")
            window_frames = max(1, sample_rate // frame_rate)
            bytes_per_frame = channels * sample_width
            consumed_frames = 0
            result: list[MouthFrame] = []
            while True:
                raw = source.readframes(window_frames)
                if not raw:
                    break
                if len(raw) % bytes_per_frame:
                    raise LipSyncError("invalid_wav")
                actual_frames = len(raw) // bytes_per_frame
                result.append(
                    MouthFrame(
                        timestamp_ms=round(consumed_frames * 1000 / sample_rate),
                        amplitude=min(1.0, _rms_from_pcm(raw, sample_width)),
                    )
                )
                consumed_frames += actual_frames
            return result
    except (EOFError, wave.Error) as error:
        raise LipSyncError("invalid_wav") from error
