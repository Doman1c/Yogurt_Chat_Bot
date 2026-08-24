from __future__ import annotations

import asyncio

from ..assets import SeparatedSong, SongAsset
from ..errors import SingingError


class DemucsSeparator:
    """Demucs candidate adapter placeholder; installation remains an optional local concern."""

    name = "demucs"

    async def separate(self, asset: SongAsset, cancelled: asyncio.Event) -> SeparatedSong:
        raise SingingError("separator_unavailable")
