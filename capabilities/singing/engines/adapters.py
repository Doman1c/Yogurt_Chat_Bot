from __future__ import annotations

import asyncio

from ..assets import VocalArtifact
from ..errors import SingingError
from ..schemas import SingingProfile
from .base import ConversionOptions


class _UnavailableEngine:
    """Candidate marker that never downloads models or accepts an engine path/URL."""

    name = "unconfigured"
    version = "unconfigured"

    async def convert(
        self,
        vocal: VocalArtifact,
        profile: SingingProfile,
        options: ConversionOptions,
        cancelled: asyncio.Event,
    ) -> VocalArtifact:
        raise SingingError("engine_unavailable")


class RVCAdapter(_UnavailableEngine):
    name = "rvc"


class SeedVCAdapter(_UnavailableEngine):
    name = "seed_vc"


class Vevo2Adapter(_UnavailableEngine):
    name = "vevo2"
