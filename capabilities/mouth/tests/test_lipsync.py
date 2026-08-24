from __future__ import annotations

import io
import math
import struct
import wave

import pytest

from capabilities.mouth.errors import LipSyncError
from capabilities.mouth import lipsync
from capabilities.mouth.lipsync import amplitude_envelope


def _wav(samples: list[int], sample_rate: int = 8000, channels: int = 1) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as target:
        target.setnchannels(channels)
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


def test_stereo_waveform_uses_all_interleaved_channels() -> None:
    frames = amplitude_envelope(_wav([0, 16000] * 400, channels=2), "audio/wav", frame_rate=25)
    assert frames
    assert max(frame.amplitude for frame in frames) > 0.3


def test_waveform_is_read_in_bounded_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    class Source:
        def __init__(self) -> None:
            self.calls: list[int] = []
            self.remaining = 25

        def __enter__(self):
            return self

        def __exit__(self, *args) -> None:
            return None

        def getnchannels(self) -> int:
            return 1

        def getsampwidth(self) -> int:
            return 2

        def getframerate(self) -> int:
            return 100

        def getcomptype(self) -> str:
            return "NONE"

        def readframes(self, count: int) -> bytes:
            self.calls.append(count)
            actual = min(count, self.remaining)
            self.remaining -= actual
            return b"\x00\x00" * actual

    source = Source()
    monkeypatch.setattr(lipsync.wave, "open", lambda *_args, **_kwargs: source)
    frames = amplitude_envelope(b"fixture", "audio/wav", frame_rate=10)
    assert len(frames) == 3
    assert source.calls == [10, 10, 10, 10]


def test_invalid_wav_is_bounded() -> None:
    with pytest.raises(LipSyncError, match="invalid_wav"):
        amplitude_envelope(b"not a wav", "audio/wav")
