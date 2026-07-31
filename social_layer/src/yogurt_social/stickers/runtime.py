from __future__ import annotations

from pathlib import Path

from .catalog import StickerCatalogLoader
from .config import StickerRuntimeConfig, load_sticker_runtime_config
from .models import StickerCandidate, StickerCatalogSnapshot
from .retriever import StickerRetriever


class StickerRuntime:
    def __init__(
        self,
        config: StickerRuntimeConfig,
        snapshot: StickerCatalogSnapshot | None,
    ) -> None:
        self.config = config
        self.snapshot = snapshot
        self.retriever = (
            StickerRetriever(
                snapshot,
                minimum_score=config.minimum_score,
                max_candidates=config.max_candidates,
            )
            if snapshot is not None
            else None
        )

    @classmethod
    def from_files(
        cls,
        config_path: str | Path,
        project_root: str | Path,
    ) -> "StickerRuntime":
        config = load_sticker_runtime_config(config_path)
        if not config.enabled:
            return cls(config, None)
        catalog_path = config.resolve_catalog_path(project_root)
        snapshot = StickerCatalogLoader(
            catalog_path,
            require_files=config.require_files,
        ).load()
        return cls(config, snapshot)

    def retrieve(
        self,
        *,
        message: str,
        scene: str,
        mood: str,
        relationship: str,
        allow_sticker: bool,
    ) -> list[StickerCandidate]:
        if not self.config.enabled or self.retriever is None:
            return []
        return self.retriever.retrieve(
            message=message,
            scene=scene,
            mood=mood,
            relationship=relationship,
            allow_sticker=allow_sticker,
        )
