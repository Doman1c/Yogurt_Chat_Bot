import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from yogurt_social.openclaw_adapter import OpenClawAdapter
from yogurt_social.stickers.models import StickerCandidate


class _CelebrationService:
    def analyze(self, user_id, message):
        return {
            "state": {
                "scene": "celebration",
                "mood": "happy",
                "relationship": "owner",
                "use_sticker": True,
            },
            "memories": [],
            "context_block": "[Yogurt Social State]\n场景：celebration",
        }


class _StubStickerRuntime:
    def retrieve(self, **kwargs):
        return [
            StickerCandidate(
                sticker_id="sticker-0123456789ab",
                file="images/sticker-0123456789ab.webp",
                description="开心举手庆祝",
                score=8.0,
                scenes=("celebration",),
                emotions=("happy",),
                tones=("cute",),
                matched_signals=("scene:celebration",),
            )
        ]


class StickerBridgeTests(unittest.TestCase):
    def test_adapter_injects_bounded_candidate_context(self):
        result = OpenClawAdapter(
            _CelebrationService(),
            sticker_runtime=_StubStickerRuntime(),
        ).prepare("owner", "成功了")

        self.assertEqual(result.metadata["sticker_candidate_count"], 1)
        self.assertEqual(result.sticker_candidates[0]["id"], "sticker-0123456789ab")
        self.assertIn("[Yogurt Sticker Candidates]", result.social_context)
        self.assertIn("choose_sticker", result.social_context)
        self.assertNotIn("images/sticker-0123456789ab.webp", result.social_context)

    def test_module_bridge_reads_tagger_catalog(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            catalog_dir = root / "assets" / "stickers"
            catalog_dir.mkdir(parents=True)
            catalog = {
                "schema_version": "1.0",
                "generated_at": "2026-08-01T00:00:00+00:00",
                "generated_by": "Yogurt Sticker Tagger",
                "asset_root": ".",
                "stickers": [
                    {
                        "id": "sticker-0123456789ab",
                        "file": "images/sticker-0123456789ab.webp",
                        "sha256": "a" * 64,
                        "status": "approved",
                        "enabled": True,
                        "description": "开心举手庆祝",
                        "ocr_text": "好耶",
                        "scenes": ["celebration"],
                        "emotions": ["happy"],
                        "tones": ["cute"],
                        "keywords": ["成功"],
                        "relationships": ["owner"],
                        "avoid_when": [],
                        "content_warnings": ["none"],
                        "weight": 1.0,
                        "cooldown_seconds": 1800,
                        "confidence": 0.95,
                        "classification": {
                            "provider": "ollama",
                            "model": "qwen3.5:9b",
                            "prompt_version": "1.0",
                            "classified_at": "2026-08-01T00:00:00+00:00",
                        },
                    }
                ],
            }
            catalog_path = catalog_dir / "stickers.json"
            catalog_path.write_text(
                json.dumps(catalog, ensure_ascii=False), encoding="utf-8"
            )
            config_path = root / "stickers-runtime.json"
            config_path.write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "enabled": True,
                        "catalog_path": "assets/stickers/stickers.json",
                        "minimum_score": 5,
                        "max_candidates": 5,
                        "require_files": False,
                        "allowed_channels": ["ilink"],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            db_path = root / "memory.sqlite3"
            request = json.dumps(
                {"user_id": "owner", "message": "成功了"}, ensure_ascii=False
            )
            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "yogurt_social.openclaw_adapter",
                    "--db",
                    str(db_path),
                    "--project-root",
                    str(root),
                    "--sticker-config",
                    str(config_path),
                ],
                input=request,
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["metadata"]["sticker_candidate_count"], 1)
        self.assertEqual(
            payload["sticker_candidates"][0]["id"], "sticker-0123456789ab"
        )
        self.assertIn("[Yogurt Sticker Candidates]", payload["social_context"])


if __name__ == "__main__":
    unittest.main()
