import importlib.util
import json
import sys
from pathlib import Path

import pytest


SCRIPT = Path(__file__).parents[1] / "runtime" / "emotion_registry.py"
SPEC = importlib.util.spec_from_file_location("emotion_registry", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def registry_file(tmp_path: Path) -> Path:
    refs = []
    for index in range(48):
        audio = tmp_path / f"ref-{index}.wav"
        audio.write_bytes(b"placeholder")
        refs.append({"id": f"r{index}", "emotion": "neutral" if index == 0 else "excited" if index == 2 else "happy", "role": "primary",
                     "audio": str(audio), "prompt_text": "test", "prompt_lang": "ja", "enabled": True})
    value = {"references": refs, "missing_emotions": ["upset"], "routing_index": {"emotions": {
        "neutral": {"primary": [{"id": "r0"}]}, "happy": {"primary": [{"id": "r1"}]}, "excited": {"primary": [{"id": "r2"}]},
    }}}
    path = tmp_path / "emotion_registry.v1.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def test_registry_validates_all_references_and_aliases(tmp_path: Path) -> None:
    registry = MODULE.EmotionRegistry(registry_file(tmp_path))
    upset = registry.route("angry")
    assert upset.canonical == "upset" and upset.used == "neutral" and upset.fallback
    assert registry.route("surprised").canonical == "excited"


def test_registry_rejects_incomplete_set(tmp_path: Path) -> None:
    path = registry_file(tmp_path)
    value = json.loads(path.read_text(encoding="utf-8"))
    value["references"].pop()
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError, match="exactly 48"):
        MODULE.EmotionRegistry(path)
