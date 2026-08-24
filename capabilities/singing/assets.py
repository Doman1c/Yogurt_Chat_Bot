from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from .errors import SingingError


@dataclass(frozen=True)
class SongAsset:
    """Trusted application-owned source asset; never serialize this object publicly."""

    asset_id: str
    local_path: Path
    media_type: str = "audio/wav"


@dataclass(frozen=True)
class VocalArtifact:
    audio: bytes
    duration_ms: int
    sample_rate: int
    media_type: str = "audio/wav"


@dataclass(frozen=True)
class InstrumentalArtifact:
    audio: bytes
    duration_ms: int
    sample_rate: int
    media_type: str = "audio/wav"


@dataclass(frozen=True)
class SeparatedSong:
    vocal: VocalArtifact
    instrumental: InstrumentalArtifact


class SingingAssetRegistry:
    """Trusted local asset composition keyed by bounded opaque IDs."""

    def __init__(self, assets: Mapping[str, SongAsset] | None = None) -> None:
        self._assets: dict[str, SongAsset] = {}
        for asset_id, asset in (assets or {}).items():
            self.register(asset_id, asset)

    def register(self, asset_id: str, asset: SongAsset) -> None:
        if asset_id != asset.asset_id or not _valid_asset_id(asset_id):
            raise ValueError("invalid_asset_id")
        if not asset.local_path.is_absolute() or asset.media_type != "audio/wav":
            raise ValueError("invalid_asset")
        self._assets[asset_id] = asset

    def resolve(self, asset_id: str) -> SongAsset:
        if not _valid_asset_id(asset_id):
            raise SingingError("unknown_asset")
        try:
            return self._assets[asset_id]
        except KeyError as error:
            raise SingingError("unknown_asset") from error

    def ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._assets))


def _valid_asset_id(asset_id: str) -> bool:
    import re

    return re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", asset_id) is not None
