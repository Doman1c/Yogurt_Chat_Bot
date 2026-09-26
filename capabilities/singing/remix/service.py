from __future__ import annotations

import asyncio
from typing import Protocol

from ..assets import InstrumentalArtifact, VocalArtifact
from ..schemas import CoverRequest


class AudioMixer(Protocol):
    async def mix(
        self,
        vocal: VocalArtifact,
        instrumental: InstrumentalArtifact,
        request: CoverRequest,
        cancelled: asyncio.Event,
    ) -> VocalArtifact:
        """Mix in-memory artifacts using bounded gains only."""


class RemixService:
    """Composition seam for a trusted in-process mixer, not a public ffmpeg wrapper."""

    def __init__(self, mixer: AudioMixer, *, supports_synchronized_transpose: bool = False) -> None:
        self._mixer = mixer
        self.supports_synchronized_transpose = supports_synchronized_transpose

    async def mix(
        self,
        vocal: VocalArtifact,
        instrumental: InstrumentalArtifact,
        request: CoverRequest,
        cancelled: asyncio.Event,
    ) -> VocalArtifact:
        return await self._mixer.mix(vocal, instrumental, request, cancelled)
