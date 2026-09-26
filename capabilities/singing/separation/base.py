from __future__ import annotations

import asyncio
from typing import Protocol

from ..assets import SeparatedSong, SongAsset


class SourceSeparator(Protocol):
    """Trusted source-separation seam; no public CLI flags or executable path."""

    async def separate(self, asset: SongAsset, cancelled: asyncio.Event) -> SeparatedSong:
        """Separate one registered source asset into in-memory vocal/instrumental stems."""
