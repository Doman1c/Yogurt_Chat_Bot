from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class StickerRecord:
    id: str
    file: str
    asset_path: Path
    sha256: str
    status: str
    enabled: bool
    description: str
    ocr_text: str
    scenes: tuple[str, ...]
    emotions: tuple[str, ...]
    tones: tuple[str, ...]
    keywords: tuple[str, ...]
    relationships: tuple[str, ...]
    avoid_when: tuple[str, ...]
    content_warnings: tuple[str, ...]
    weight: float
    cooldown_seconds: int
    confidence: float


@dataclass(frozen=True)
class StickerCatalogSnapshot:
    schema_version: str
    generated_at: str
    generated_by: str | None
    source_path: Path
    asset_root: Path
    stickers: tuple[StickerRecord, ...]


@dataclass(frozen=True)
class StickerCandidate:
    sticker_id: str
    file: str
    description: str
    score: float
    scenes: tuple[str, ...]
    emotions: tuple[str, ...]
    tones: tuple[str, ...]
    matched_signals: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.sticker_id,
            "file": self.file,
            "description": self.description,
            "score": self.score,
            "scenes": list(self.scenes),
            "emotions": list(self.emotions),
            "tones": list(self.tones),
            "matched_signals": list(self.matched_signals),
        }
