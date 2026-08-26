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


def registry_file(tmp_path: Path, *, count: int = 48, nested: bool = False) -> tuple[Path, Path]:
    reference_root = tmp_path / "private"
    refs = []
    for index in range(count):
        audio = reference_root / "references" / "happy" / ("primary" if index in {0, 1} else "secondary") / f"ref-{index}.wav"
        audio.parent.mkdir(parents=True, exist_ok=True)
        audio.write_bytes(b"placeholder")
        refs.append({"id": f"r{index}", "emotion": "neutral" if index == 0 else "excited" if index == 2 else "happy", "role": "primary" if index in {0, 1, 2} else "secondary",
                     "audio": str(audio.relative_to(reference_root)), "prompt_text": "test", "prompt_lang": "ja", "enabled": True,
                     "human_intensity_hint": index / max(count - 1, 1), "runtime_intensity": None})
    direct = {"neutral": {"primary": [{"id": "r0"}]}, "excited": {"primary": [{"id": "r2"}]},
              "happy": {"primary": [{"id": "r1"}], "secondary": [{"id": f"r{i}"} for i in range(3, count)]}}
    value = {"references": refs, "missing_emotions": ["upset"], "routing_index": {"emotions": direct} if nested else direct}
    path = tmp_path / "registry" / "emotion_registry.v1.json"
    path.parent.mkdir()
    path.write_text(json.dumps(value), encoding="utf-8")
    return path, reference_root


def test_relative_paths_and_direct_routing_are_cwd_independent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path, root = registry_file(tmp_path)
    monkeypatch.chdir(tmp_path / "registry")
    registry = MODULE.EmotionRegistry(path, root)
    assert len(registry.references) == 48
    assert all(Path(row["resolved_path"]).is_absolute() and row["exists"] for row in registry.validation_rows)
    upset = registry.route("angry")
    assert upset.canonical == "upset" and upset.used == "neutral" and upset.fallback
    assert registry.route("surprised").canonical == "excited"


def test_nested_schema_and_intensity_select_different_human_rank(tmp_path: Path) -> None:
    path, root = registry_file(tmp_path, nested=True)
    registry = MODULE.EmotionRegistry(path, root)
    assert registry.route("happy").selection_mode == "primary_default"
    assert registry.route("happy", intensity=0.0).reference_id != registry.route("happy", intensity=1.0).reference_id
    assert registry.route("happy", intensity=0.1).selection_mode == "human_rank_provisional"
    assert registry.public_profiles()[0]["primary_count"] == 1


def test_schema_allows_future_49th_reference(tmp_path: Path) -> None:
    path, root = registry_file(tmp_path, count=49)
    assert len(MODULE.EmotionRegistry(path, root).references) == 49


def test_semantic_mismatch_fails_closed(tmp_path: Path) -> None:
    path, root = registry_file(tmp_path)
    value = json.loads(path.read_text(encoding="utf-8"))
    value["references"][1]["emotion"] = "serious"
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError, match="routing_semantic_mismatch"):
        MODULE.EmotionRegistry(path, root)


def test_routing_role_mismatch_fails_closed(tmp_path: Path) -> None:
    path, root = registry_file(tmp_path)
    value = json.loads(path.read_text(encoding="utf-8"))
    value["references"][1]["role"] = "secondary"
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError, match="routing_semantic_mismatch"):
        MODULE.EmotionRegistry(path, root)


def test_historical_id_is_not_semantic_metadata_and_fallback_flags_are_correct(tmp_path: Path) -> None:
    path, root = registry_file(tmp_path)
    value = json.loads(path.read_text(encoding="utf-8"))
    value["references"][1]["id"] = "serious_historical_id"
    for row in value["routing_index"]["happy"]["primary"]:
        row["id"] = "serious_historical_id"
    path.write_text(json.dumps(value), encoding="utf-8")
    registry = MODULE.EmotionRegistry(path, root)
    assert registry.route("happy").used == "happy"
    unknown = registry.route("foobar")
    assert (unknown.canonical, unknown.used, unknown.fallback, unknown.fallback_reason) == ("neutral", "neutral", True, "unknown_emotion")
    assert registry.route("upset").fallback
    assert not registry.route("neutral").fallback
