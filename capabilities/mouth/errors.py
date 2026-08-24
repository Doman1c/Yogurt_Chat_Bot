from __future__ import annotations


class MouthError(RuntimeError):
    """Base error for local Mouth-only implementation failures."""


class PlaybackError(MouthError):
    """A bounded playback failure; never expose command or filesystem detail."""


class LipSyncError(MouthError):
    """A bounded error while reading a WAV amplitude envelope."""
