from __future__ import annotations

import io
import math
import struct
import wave

import pytest

from capabilities.mouth.errors import LipSyncError
from capabilities.mouth.lipsync import amplitude_envelope


def _wav(samples: list[int], sample_rate: int = 8000) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(sample_rate)
        target.writeframes(b"".join(struct.pack("<h", item) for item in samples))
    return output.getvalue()


def test_silence_has_zero_amplitude_and_monotonic_frames() -> None:
    frames = amplitude_envelope(_wav([0] * 800), "audio/wav", frame_rate=25)
    assert frames
    assert max(frame.amplitude for frame in frames) == pytest.approx(0.0)
    assert [frame.timestamp_ms for frame in frames] == sorted(frame.timestamp_ms for frame in frames)


def test_waveform_has_bounded_nonzero_amplitude() -> None:
    samples = [int(12000 * math.sin(index / 8)) for index in range(800)]
    frames = amplitude_envelope(_wav(samples), "audio/wav", frame_rate=30)
    assert len(frames) in {3, 4}
    assert max(frame.amplitude for frame in frames) > 0.2
    assert all(0.0 <= frame.amplitude <= 1.0 for frame in frames)


def test_invalid_wav_is_bounded() -> None:
    with pytest.raises(LipSyncError, match="invalid_wav"):
        amplitude_envelope(b"not a wav", "audio/wav")
