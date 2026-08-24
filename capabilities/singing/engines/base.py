from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Protocol

from ..assets import VocalArtifact
from ..schemas import ExpressionPreservation, SingingProfile


@dataclass(frozen=True)
class ConversionOptions:
    transpose_semitones: int
    preserve_pitch: bool
    preserve_expression: bool
    expression_preservation: ExpressionPreservation


class SingingConversionEngine(Protocol):
    """Trusted SVC seam; engine installation and checkpoints are application-owned."""

    name: str
    version: str

    async def convert(
        self,
        vocal: VocalArtifact,
        profile: SingingProfile,
        options: ConversionOptions,
        cancelled: asyncio.Event,
    ) -> VocalArtifact:
        """Return converted in-memory vocal or a bounded SingingError."""
