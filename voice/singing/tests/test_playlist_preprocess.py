from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


SCRIPT = Path(__file__).parents[1] / "scripts" / "prepare_playlist_input.py"
SPEC = importlib.util.spec_from_file_location("playlist_preprocess", SCRIPT)
PLAYLIST = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = PLAYLIST
assert SPEC.loader
SPEC.loader.exec_module(PLAYLIST)


def test_parse_timestamp_accepts_playlist_formats() -> None:
    assert PLAYLIST.parse_timestamp("21") == 21.0
    assert PLAYLIST.parse_timestamp("01:44") == 104.0
    assert PLAYLIST.parse_timestamp("00:31:49") == 1909.0
    assert PLAYLIST.parse_timestamp("02:03.500") == 123.5


def test_text_chapters_infer_next_start_as_end() -> None:
    chapters = PLAYLIST.parse_text_chapters(
        """
        # user supplied chapters
        00:00 | Opening
        03:12.500 | Second Song
        07:45 | Third Song
        """
    )
    assert [item.start for item in chapters] == [0.0, 192.5, 465.0]
    assert chapters[0].title == "Opening"
    assert chapters[0].end is None
    assert chapters[-1].end is None


def test_text_chapters_support_explicit_end() -> None:
    chapters = PLAYLIST.parse_text_chapters(
        "00:10 --> 02:00 | Song A\n02:05 --> 04:00 | Song B\n"
    )
    assert chapters[0].start == 10.0
    assert chapters[0].end == 120.0
    assert chapters[1].start == 125.0
    assert chapters[1].end == 240.0


def test_json_chapters_support_object_wrapper() -> None:
    chapters = PLAYLIST.parse_json_chapters(
        {"chapters": [
            {"start": "00:00", "title": "A"},
            {"start": "02:30", "title": "B", "end": "05:00"},
        ]}
    )
    assert chapters[0].title == "A"
    assert chapters[1].start == 150.0
    assert chapters[1].end == 300.0


def test_chapters_reject_non_monotonic_and_overlap() -> None:
    with pytest.raises(ValueError, match="strictly increasing"):
        PLAYLIST.parse_text_chapters("02:00 | B\n01:00 | A\n")
    with pytest.raises(ValueError, match="overlaps"):
        PLAYLIST.parse_text_chapters("00:00 --> 02:30 | A\n02:00 | B\n")


def test_sidecar_discovery_supports_full_name_and_stem(tmp_path: Path) -> None:
    audio = tmp_path / "playlist.weba"
    audio.write_bytes(b"audio")
    sidecar = tmp_path / "playlist.chapters.txt"
    sidecar.write_text("00:00 | Song", encoding="utf-8")
    assert PLAYLIST.find_sidecar(audio, PLAYLIST.DEFAULT_SIDECAR_SUFFIXES) == sidecar


def test_sidecar_discovery_rejects_ambiguity(tmp_path: Path) -> None:
    audio = tmp_path / "playlist.weba"
    audio.write_bytes(b"audio")
    (tmp_path / "playlist.chapters.txt").write_text("00:00 | A", encoding="utf-8")
    (tmp_path / "playlist.weba.chapters.txt").write_text("00:00 | A", encoding="utf-8")
    with pytest.raises(ValueError, match="multiple chapter sidecars"):
        PLAYLIST.find_sidecar(audio, PLAYLIST.DEFAULT_SIDECAR_SUFFIXES)


def test_track_filename_is_unique_per_source_hash() -> None:
    source = Path("My Playlist.weba")
    first = PLAYLIST.track_filename(source, "playlist-aaaaaaaaaa", 1, "Song One")
    second = PLAYLIST.track_filename(source, "playlist-bbbbbbbbbb", 1, "Song One")
    assert first.endswith(".wav")
    assert first != second
    assert "01" in first


def test_policy_defaults_enable_extended_inputs(tmp_path: Path) -> None:
    cfg = {
        "runtime_root": str(tmp_path),
        "incoming_dir": str(tmp_path / "incoming"),
        "stable_seconds": 2,
    }
    policy = PLAYLIST._playlist_policy(cfg)
    assert policy["enabled"] is True
    assert {".weba", ".webm", ".ogg", ".opus"}.issubset(policy["extra_input_extensions"])
