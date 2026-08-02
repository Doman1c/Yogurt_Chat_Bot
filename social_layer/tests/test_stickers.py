from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from yogurt_social.stickers import (
    StickerCatalogError,
    StickerCatalogLoader,
    StickerRuntime,
    load_sticker_runtime_config,
)


def _sticker(
    sticker_id: str,
    *,
    status: str = "approved",
    enabled: bool = True,
    scenes: list[str] | None = None,
    emotions: list[str] | None = None,
    keywords: list[str] | None = None,
    relationships: list[str] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, object]:
    suffix = sticker_id.removeprefix("sticker-")
    return {
        "id": sticker_id,
        "file": f"images/{sticker_id}.webp",
        "sha256": (suffix * 6)[:64],
        "status": status,
        "enabled": enabled,
        "description": f"{sticker_id} 示例",
        "ocr_text": "",
        "scenes": scenes or ["celebration"],
        "emotions": emotions or ["happy"],
        "tones": ["cute"],
        "keywords": keywords or ["成功", "完成"],
        "relationships": relationships or ["owner"],
        "avoid_when": [],
        "content_warnings": warnings or ["none"],
        "weight": 1.0,
        "cooldown_seconds": 1800,
        "confidence": 0.9,
        "classification": {
            "provider": "mock",
            "model": "mock-v1",
            "prompt_version": "1.0",
            "classified_at": "2026-08-01T00:00:00+00:00",
        },
    }


class StickerRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.asset_root = self.root / "assets" / "stickers"
        (self.asset_root / "images").mkdir(parents=True)
        self.catalog_path = self.asset_root / "stickers.json"
        self.config_path = self.root / "stickers.runtime.json"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def write_catalog(self, stickers: list[dict[str, object]]) -> None:
        for sticker in stickers:
            file_path = self.asset_root / str(sticker["file"])
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_bytes(b"RIFF-test-WEBP")
        self.catalog_path.write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "generated_at": "2026-08-01T00:00:00+00:00",
                    "generated_by": "test",
                    "asset_root": ".",
                    "stickers": stickers,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    def write_config(self, *, enabled: bool = True, require_files: bool = True) -> None:
        self.config_path.write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "enabled": enabled,
                    "catalog_path": "assets/stickers/stickers.json",
                    "minimum_score": 5,
                    "max_candidates": 3,
                    "require_files": require_files,
                    "global_cooldown_seconds": 300,
                    "scene_cooldown_seconds": 900,
                    "default_sticker_cooldown_seconds": 1800,
                    "max_stickers_per_hour": 4,
                    "allowed_channels": ["ilink"],
                }
            ),
            encoding="utf-8",
        )

    def test_runtime_config_is_separate_from_catalog(self) -> None:
        self.write_config()
        config = load_sticker_runtime_config(self.config_path)
        self.assertEqual(config.catalog_path, "assets/stickers/stickers.json")
        self.assertEqual(config.max_candidates, 3)

    def test_loader_accepts_tagger_catalog_contract(self) -> None:
        sticker = _sticker("sticker-111111111111")
        self.write_catalog([sticker])
        snapshot = StickerCatalogLoader(self.catalog_path).load()
        self.assertEqual(snapshot.schema_version, "1.0")
        self.assertEqual(snapshot.stickers[0].tones, ("cute",))
        self.assertEqual(snapshot.stickers[0].content_warnings, ("none",))

    def test_loader_rejects_path_traversal(self) -> None:
        sticker = _sticker("sticker-222222222222")
        sticker["file"] = "images/../../outside.webp"
        self.catalog_path.write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "generated_at": "2026-08-01T00:00:00+00:00",
                    "asset_root": ".",
                    "stickers": [sticker],
                }
            ),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(StickerCatalogError, "逃逸"):
            StickerCatalogLoader(self.catalog_path, require_files=False).load()

    def test_loader_rejects_asset_root_escape(self) -> None:
        sticker = _sticker("sticker-888888888888")
        self.catalog_path.write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "generated_at": "2026-08-01T00:00:00+00:00",
                    "asset_root": "../../outside",
                    "stickers": [sticker],
                }
            ),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(StickerCatalogError, "asset_root"):
            StickerCatalogLoader(self.catalog_path, require_files=False).load()

    def test_retriever_filters_and_ranks_candidates(self) -> None:
        approved = _sticker("sticker-333333333333")
        pending = _sticker("sticker-444444444444", status="pending")
        risky = _sticker("sticker-555555555555", warnings=["medical"])
        wrong_relationship = _sticker(
            "sticker-666666666666", relationships=["new"]
        )
        self.write_catalog([approved, pending, risky, wrong_relationship])
        self.write_config()

        runtime = StickerRuntime.from_files(self.config_path, self.root)
        candidates = runtime.retrieve(
            message="终于成功完成了",
            scene="celebration",
            mood="happy",
            relationship="owner",
            allow_sticker=True,
        )
        self.assertEqual([item.sticker_id for item in candidates], ["sticker-333333333333"])
        self.assertGreaterEqual(candidates[0].score, 5)
        self.assertIn("scene:celebration", candidates[0].matched_signals)

    def test_disabled_runtime_does_not_require_catalog(self) -> None:
        self.write_config(enabled=False)
        runtime = StickerRuntime.from_files(self.config_path, self.root)
        self.assertEqual(
            runtime.retrieve(
                message="成功了",
                scene="celebration",
                mood="happy",
                relationship="owner",
                allow_sticker=True,
            ),
            [],
        )

    def test_router_can_block_sticker_candidates(self) -> None:
        self.write_catalog([_sticker("sticker-777777777777")])
        self.write_config()
        runtime = StickerRuntime.from_files(self.config_path, self.root)
        self.assertEqual(
            runtime.retrieve(
                message="成功了",
                scene="celebration",
                mood="happy",
                relationship="owner",
                allow_sticker=False,
            ),
            [],
        )


if __name__ == "__main__":
    unittest.main()
