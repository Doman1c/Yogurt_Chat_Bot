from __future__ import annotations

import io
import math
import struct
import wave
from typing import Iterable

from pydantic import BaseModel, Field

from .errors import LipSyncError


class MouthFrame(BaseModel):
    """Amplitude-only lipsync frame; it is not a phoneme or viseme."""

    timestamp_ms: int = Field(ge=0)
    amplitude: float = Field(ge=0.0, le=1.0)


def _samples(raw: bytes, sample_width: int) -> Iterable[float]:
    if sample_width == 1:
        for value in raw:
            yield (value - 128) / 128.0
        return
    if sample_width == 2:
        for (value,) in struct.iter_unpack("<h", raw):
            yield value / 32768.0
        return
    if sample_width == 3:
        for index in range(0, len(raw) - 2, 3):
            value = int.from_bytes(raw[index : index + 3], "little", signed=True)
            yield value / 8388608.0
        return
    if sample_width == 4:
        for (value,) in struct.iter_unpack("<i", raw):
            yield value / 2147483648.0
        return
    raise LipSyncError("unsupported_wav_format")


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
            frames = source.getnframes()
            if source.getcomptype() != "NONE" or not (1 <= channels <= 8):
                raise LipSyncError("unsupported_wav_format")
            if not (1 <= sample_rate <= 192000) or frames < 0:
                raise LipSyncError("invalid_wav")
            raw = source.readframes(frames)
    except (EOFError, wave.Error) as error:
        raise LipSyncError("invalid_wav") from error

    samples_per_window = max(1, sample_rate // frame_rate) * channels
    values = list(_samples(raw, sample_width))
    if len(values) != frames * channels:
        raise LipSyncError("invalid_wav")
    result: list[MouthFrame] = []
    for offset in range(0, len(values), samples_per_window):
        window = values[offset : offset + samples_per_window]
        if not window:
            continue
        rms = math.sqrt(sum(value * value for value in window) / len(window))
        timestamp_ms = round((offset // channels) * 1000 / sample_rate)
        result.append(MouthFrame(timestamp_ms=timestamp_ms, amplitude=min(1.0, rms)))
    return result
