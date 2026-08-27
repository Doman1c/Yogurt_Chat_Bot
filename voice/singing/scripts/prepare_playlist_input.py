"""Prepare extended audio containers and timestamped playlists for Singing Pipeline v2.1.

The main Singing pipeline intentionally keeps its managed inbox contract simple:
one supported audio file equals one job.  This preprocessor runs immediately before
that pipeline and converts extra containers to 44.1 kHz PCM WAV.  When a chapter
sidecar is present it splits the source into independent WAV jobs using the user
provided timestamps.

No separator or Seed-VC model is loaded here.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

PIPELINE_INPUTS = {".mp3", ".wav", ".flac", ".m4a"}
DEFAULT_EXTRA_INPUTS = {".weba", ".webm", ".ogg", ".opus"}
DEFAULT_SIDECAR_SUFFIXES = (".chapters.txt", ".chapters.json")
IGNORED_SUFFIXES = {".tmp", ".part", ".crdownload"}
SAMPLE_RATE = 44_100
MAX_CHAPTERS = 200


@dataclass(frozen=True)
class Chapter:
    start: float
    title: str
    end: float | None = None


@dataclass(frozen=True)
class PreparedTrack:
    index: int
    title: str
    start: float
    end: float | None
    filename: str


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_name(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9._\-\u4e00-\u9fff\u3040-\u30ff]+", "_", value)
    return normalized.strip("._")[:96] or "track"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def parse_timestamp(value: str | int | float) -> float:
    if isinstance(value, (int, float)):
        seconds = float(value)
    else:
        raw = str(value).strip()
        if not raw:
            raise ValueError("empty chapter timestamp")
        parts = raw.split(":")
        try:
            if len(parts) == 1:
                seconds = float(parts[0])
            elif len(parts) == 2:
                minutes, sec = parts
                seconds = int(minutes) * 60 + float(sec)
            elif len(parts) == 3:
                hours, minutes, sec = parts
                seconds = int(hours) * 3600 + int(minutes) * 60 + float(sec)
            else:
                raise ValueError
        except ValueError as exc:
            raise ValueError(f"invalid chapter timestamp: {value!r}") from exc
    if seconds < 0:
        raise ValueError("chapter timestamps cannot be negative")
    return round(seconds, 6)


def _chapter(start: Any, title: Any, end: Any = None) -> Chapter:
    name = str(title or "").strip()
    if not name:
        name = "Track"
    return Chapter(
        start=parse_timestamp(start),
        title=name[:160],
        end=parse_timestamp(end) if end not in (None, "") else None,
    )


def parse_text_chapters(text: str) -> list[Chapter]:
    chapters: list[Chapter] = []
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "|" not in line:
            raise ValueError(
                f"chapter line {line_number} must use 'timestamp | title' or "
                "'start --> end | title'"
            )
        timing, title = (part.strip() for part in line.split("|", 1))
        if "-->" in timing:
            start, end = (part.strip() for part in timing.split("-->", 1))
            chapters.append(_chapter(start, title, end))
        else:
            chapters.append(_chapter(timing, title))
    return validate_chapters(chapters)


def parse_json_chapters(payload: Any) -> list[Chapter]:
    if isinstance(payload, dict):
        payload = payload.get("chapters")
    if not isinstance(payload, list):
        raise ValueError("chapter JSON must be a list or an object containing 'chapters'")
    chapters: list[Chapter] = []
    for index, item in enumerate(payload, start=1):
        if not isinstance(item, dict) or "start" not in item:
            raise ValueError(f"chapter JSON item {index} must contain 'start'")
        chapters.append(_chapter(item["start"], item.get("title") or f"Track {index:02d}", item.get("end")))
    return validate_chapters(chapters)


def validate_chapters(chapters: Iterable[Chapter], *, min_track_seconds: float = 1.0) -> list[Chapter]:
    items = list(chapters)
    if not items:
        raise ValueError("chapter list is empty")
    if len(items) > MAX_CHAPTERS:
        raise ValueError(f"chapter list exceeds {MAX_CHAPTERS} tracks")
    previous_start = -1.0
    for index, item in enumerate(items):
        if item.start <= previous_start:
            raise ValueError("chapter start timestamps must be strictly increasing")
        previous_start = item.start
        next_start = items[index + 1].start if index + 1 < len(items) else None
        effective_end = item.end if item.end is not None else next_start
        if item.end is not None and item.end <= item.start:
            raise ValueError(f"chapter {index + 1} end must be after start")
        if next_start is not None and item.end is not None and item.end > next_start + 1e-6:
            raise ValueError(f"chapter {index + 1} explicit end overlaps the next chapter")
        if effective_end is not None and effective_end - item.start < min_track_seconds:
            raise ValueError(f"chapter {index + 1} is shorter than {min_track_seconds:g} seconds")
    return items


def load_chapters(path: Path, *, min_track_seconds: float) -> list[Chapter]:
    if path.name.lower().endswith(".json"):
        result = parse_json_chapters(json.loads(path.read_text(encoding="utf-8-sig")))
    else:
        result = parse_text_chapters(path.read_text(encoding="utf-8-sig"))
    return validate_chapters(result, min_track_seconds=min_track_seconds)


def sidecar_candidates(audio: Path, suffixes: Iterable[str]) -> list[Path]:
    candidates: list[Path] = []
    for suffix in suffixes:
        suffix = str(suffix)
        candidates.append(audio.with_name(audio.name + suffix))
        candidates.append(audio.with_name(audio.stem + suffix))
    unique: list[Path] = []
    seen: set[str] = set()
    for item in candidates:
        key = str(item).casefold()
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique


def find_sidecar(audio: Path, suffixes: Iterable[str]) -> Path | None:
    existing = [path for path in sidecar_candidates(audio, suffixes) if path.is_file()]
    if len(existing) > 1:
        names = ", ".join(path.name for path in existing)
        raise ValueError(f"multiple chapter sidecars found for {audio.name}: {names}")
    return existing[0] if existing else None


def wait_until_stable(path: Path, seconds: float) -> bool:
    if seconds < 1 or seconds > 10:
        raise ValueError("playlist stable_seconds must be between 1 and 10")
    try:
        before = (path.stat().st_size, path.stat().st_mtime_ns)
    except FileNotFoundError:
        return False
    time.sleep(seconds)
    try:
        after = (path.stat().st_size, path.stat().st_mtime_ns)
    except FileNotFoundError:
        return False
    return before == after and after[0] > 0


def load_config(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    base = path.parent
    for key in ("runtime_root", "incoming_dir"):
        if key in data:
            item = Path(data[key])
            data[key] = str(item if item.is_absolute() else (base / item).resolve())
    separation = data.get("separation") or {}
    for key in ("python",):
        if key in separation and separation[key]:
            item = Path(separation[key])
            separation[key] = str(item if item.is_absolute() else (base / item).resolve())
    return data


def find_ffmpeg(cfg: dict[str, Any]) -> Path:
    configured = str(cfg.get("ffmpeg", "auto"))
    if configured.lower() != "auto":
        candidate = Path(configured)
        if candidate.is_file():
            return candidate
        located = shutil.which(configured)
        if located:
            return Path(located)
        raise RuntimeError(f"configured ffmpeg not found: {configured}")
    separation_python = Path((cfg.get("separation") or {}).get("python", ""))
    if separation_python.is_file():
        code = "import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())"
        try:
            result = subprocess.check_output([str(separation_python), "-c", code], text=True).strip()
            candidate = Path(result)
            if candidate.is_file():
                return candidate
        except (OSError, subprocess.CalledProcessError):
            pass
    located = shutil.which("ffmpeg")
    if located:
        return Path(located)
    raise RuntimeError("ffmpeg not found; configure 'ffmpeg' or keep imageio_ffmpeg in the separation environment")


def run_ffmpeg(ffmpeg: Path, source: Path, output: Path, *, start: float = 0.0,
               end: float | None = None) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    command = [str(ffmpeg), "-hide_banner", "-loglevel", "error", "-y", "-i", str(source)]
    if start > 0:
        command.extend(["-ss", f"{start:.6f}"])
    if end is not None:
        command.extend(["-t", f"{end - start:.6f}"])
    command.extend(["-vn", "-ar", str(SAMPLE_RATE), "-ac", "2", "-c:a", "pcm_s24le", str(output)])
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if result.returncode:
        detail = result.stderr.strip()[-1200:]
        raise RuntimeError(f"ffmpeg failed for {source.name}: {detail}")
    if not output.is_file() or output.stat().st_size <= 44:
        raise RuntimeError(f"ffmpeg produced no usable WAV for {source.name}")


def unique_destination(directory: Path, filename: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    candidate = directory / filename
    index = 2
    while candidate.exists():
        candidate = directory / f"{Path(filename).stem}-{index}{Path(filename).suffix}"
        index += 1
    return candidate


def track_filename(source: Path, source_id: str, index: int, title: str) -> str:
    return f"{safe_name(source.stem)}__{index:02d}__{safe_name(title)}__{source_id[:8]}.wav"


def _playlist_policy(cfg: dict[str, Any]) -> dict[str, Any]:
    raw = cfg.get("playlist")
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ValueError("playlist config must be an object")
    runtime_root = Path(cfg["runtime_root"])
    source_archive = Path(raw.get("source_archive_dir") or runtime_root / "playlist_sources")
    if not source_archive.is_absolute():
        source_archive = (runtime_root / source_archive).resolve()
    extra = {str(item).lower() for item in raw.get("extra_input_extensions", DEFAULT_EXTRA_INPUTS)}
    extra = {item if item.startswith(".") else f".{item}" for item in extra}
    suffixes = tuple(str(item) for item in raw.get("sidecar_suffixes", DEFAULT_SIDECAR_SUFFIXES))
    return {
        "enabled": bool(raw.get("enabled", True)),
        "source_archive_dir": source_archive,
        "extra_input_extensions": extra,
        "sidecar_suffixes": suffixes,
        "min_track_seconds": float(raw.get("min_track_seconds", 5.0)),
        "stable_seconds": float(raw.get("stable_seconds", cfg.get("stable_seconds", 2))),
    }


def _archive_source(source: Path, sidecar: Path | None, destination: Path) -> tuple[Path, Path | None]:
    source_dir = destination / "source"
    source_dir.mkdir(parents=True, exist_ok=False)
    archived_source = source_dir / source.name
    os.replace(source, archived_source)
    archived_sidecar: Path | None = None
    if sidecar is not None and sidecar.exists():
        archived_sidecar = source_dir / sidecar.name
        os.replace(sidecar, archived_sidecar)
    return archived_source, archived_sidecar


def prepare_source(cfg: dict[str, Any], source: Path, sidecar: Path | None, *, ffmpeg: Path,
                   policy: dict[str, Any]) -> list[Path]:
    source_hash = sha256_file(source)
    source_id = f"{safe_name(source.stem)}-{source_hash[:10]}"
    incoming = Path(cfg["incoming_dir"])
    staging = incoming / ".playlist-staging" / source_id
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True, exist_ok=False)
    archive_root = Path(policy["source_archive_dir"])
    archive_dir = unique_destination(archive_root, source_id)
    # unique_destination returns a path shaped like a file; for this purpose it
    # is simply a unique not-yet-existing directory path.
    if archive_dir.exists():
        raise RuntimeError(f"playlist source archive already exists: {archive_dir}")

    chapter_list: list[Chapter] | None = None
    if sidecar is not None:
        chapter_list = load_chapters(sidecar, min_track_seconds=float(policy["min_track_seconds"]))

    prepared: list[PreparedTrack] = []
    staged_files: list[Path] = []
    try:
        if chapter_list:
            for index, chapter in enumerate(chapter_list, start=1):
                inferred_end = chapter_list[index].start if index < len(chapter_list) else None
                end = chapter.end if chapter.end is not None else inferred_end
                filename = track_filename(source, source_id, index, chapter.title)
                output = staging / filename
                run_ffmpeg(ffmpeg, source, output, start=chapter.start, end=end)
                staged_files.append(output)
                prepared.append(PreparedTrack(index, chapter.title, chapter.start, end, filename))
        else:
            filename = f"{safe_name(source.stem)}__converted__{source_hash[:8]}.wav"
            output = staging / filename
            run_ffmpeg(ffmpeg, source, output)
            staged_files.append(output)
            prepared.append(PreparedTrack(1, source.stem, 0.0, None, filename))

        archived_source, archived_sidecar = _archive_source(source, sidecar, archive_dir)
        published: list[Path] = []
        for item in staged_files:
            destination = unique_destination(incoming, item.name)
            os.replace(item, destination)
            published.append(destination)

        manifest = {
            "version": 1,
            "created_at": now(),
            "source_sha256": source_hash,
            "source_filename": archived_source.name,
            "sidecar_filename": archived_sidecar.name if archived_sidecar else None,
            "mode": "chapters" if chapter_list else "transcode",
            "sample_rate": SAMPLE_RATE,
            "tracks": [asdict(item) for item in prepared],
        }
        (archive_dir / "playlist_manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        try:
            staging.rmdir()
            staging.parent.rmdir()
        except OSError:
            pass
        return published
    except Exception:
        # Staging contains only derived WAVs.  Never delete or move the source
        # on an FFmpeg/validation failure before _archive_source succeeds.
        raise


def preprocess(cfg: dict[str, Any]) -> tuple[int, int]:
    policy = _playlist_policy(cfg)
    if not policy["enabled"]:
        return 0, 0
    incoming = Path(cfg["incoming_dir"])
    incoming.mkdir(parents=True, exist_ok=True)
    Path(policy["source_archive_dir"]).mkdir(parents=True, exist_ok=True)
    ffmpeg = find_ffmpeg(cfg)
    extensions = PIPELINE_INPUTS | set(policy["extra_input_extensions"])
    prepared_sources = 0
    prepared_tracks = 0
    errors: list[str] = []

    for source in sorted(incoming.iterdir()):
        if not source.is_file() or source.name.startswith("."):
            continue
        suffix = source.suffix.lower()
        if suffix in IGNORED_SUFFIXES or suffix not in extensions:
            continue
        try:
            sidecar = find_sidecar(source, policy["sidecar_suffixes"])
            needs_transcode = suffix not in PIPELINE_INPUTS
            if sidecar is None and not needs_transcode:
                continue
            if not wait_until_stable(source, float(policy["stable_seconds"])):
                print(f"[PLAYLIST_WAITING_FOR_STABLE_FILE] {source.name}")
                continue
            if sidecar is not None and not wait_until_stable(sidecar, float(policy["stable_seconds"])):
                print(f"[PLAYLIST_WAITING_FOR_STABLE_SIDECAR] {sidecar.name}")
                continue
            published = prepare_source(cfg, source, sidecar, ffmpeg=ffmpeg, policy=policy)
            prepared_sources += 1
            prepared_tracks += len(published)
            mode = "split" if sidecar is not None else "transcoded"
            print(f"[PLAYLIST_{mode.upper()}] {source.name} -> {len(published)} WAV job(s)")
            for item in published:
                print(f"  {item.name}")
        except Exception as exc:
            errors.append(f"{source.name}: {exc}")
            print(f"[PLAYLIST_PREPROCESS_FAILED] {source.name}: {exc}", file=sys.stderr)

    if errors:
        raise RuntimeError("playlist preprocessing failed: " + " | ".join(errors))
    return prepared_sources, prepared_tracks


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    try:
        cfg = load_config(args.config.resolve())
        sources, tracks = preprocess(cfg)
        if sources:
            print(f"Prepared {sources} source(s) into {tracks} Singing job(s).")
        return 0
    except Exception as exc:
        print(f"Playlist preprocessing failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
