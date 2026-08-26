"""Sequential, failure-isolated singing conversion pipeline.

This module deliberately orchestrates the existing ``run_seed_vc.py`` wrapper;
it never imports, modifies, or reimplements Seed-VC inference.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

if str(Path(__file__).parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).parent))
from pipeline_state import JobHistory, PipelineAlreadyRunning, PipelineLock, sha256_file, write_json


SUPPORTED_INPUTS = {".mp3", ".wav", ".flac", ".m4a"}
SAMPLE_RATE = 44_100
PIPELINE_VERSION = "2.1"
IGNORED_SUFFIXES = {".tmp", ".part", ".crdownload"}
HARMONY_MODES = {"preserve", "remove"}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def json_dump(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def resolved(value: str | Path, base: Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else (base / path).resolve()


def run(command: list[str], *, log: Path, cwd: Path | None = None) -> None:
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as handle:
        handle.write("$ " + subprocess.list2cmdline(command) + "\n")
        result = subprocess.run(command, cwd=cwd, stdout=handle, stderr=subprocess.STDOUT, text=True)
    if result.returncode:
        raise RuntimeError(f"command failed ({result.returncode}); see {log}")


def audio_metrics(python: Path, path: Path) -> dict[str, float | int]:
    """Use the isolated audio stack so FLOAT WAVs are checked without changing Seed-VC."""
    program = (
        "import json,sys,numpy as np,soundfile as sf; "
        "x,sr=sf.read(sys.argv[1],always_2d=True,dtype='float64'); "
        "print(json.dumps({'sample_rate':int(sr),'channels':int(x.shape[1]),'frames':int(x.shape[0]),"
        "'duration':float(x.shape[0]/sr),'peak':float(np.max(np.abs(x))) if x.size else 0.0,"
        "'rms':float(np.sqrt(np.mean(np.square(x)))) if x.size else 0.0,'finite':bool(np.isfinite(x).all())}))"
    )
    raw = subprocess.check_output([str(python), "-c", program, str(path)], text=True).strip()
    return json.loads(raw)


def validate_audio(python: Path, path: Path, *, expected_duration: float | None = None,
                   expected_rate: int | None = SAMPLE_RATE) -> dict[str, Any]:
    if not path.is_file() or path.stat().st_size == 0:
        raise RuntimeError(f"missing or empty audio: {path}")
    metrics = audio_metrics(python, path)
    if not metrics["finite"] or metrics["duration"] <= 0 or metrics["rms"] < 1e-6:
        raise RuntimeError(f"invalid/silent/non-finite audio: {path}")
    if expected_rate is not None and metrics["sample_rate"] != expected_rate:
        raise RuntimeError(f"unexpected sample rate {metrics['sample_rate']} for {path}; expected {expected_rate}")
    if expected_duration is not None and abs(float(metrics["duration"]) - expected_duration) > 0.25:
        raise RuntimeError(f"duration drift exceeds 250 ms for {path}")
    if metrics["peak"] > 1.25:
        raise RuntimeError(f"catastrophic clipping in {path} (peak {metrics['peak']:.3f})")
    return metrics


def atomic_copy(source: Path, destination: Path, python: Path, *, duration: float | None = None,
                expected_rate: int | None = SAMPLE_RATE) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".tmp")
    shutil.copyfile(source, temporary)
    validate_audio(python, temporary, expected_duration=duration, expected_rate=expected_rate)
    os.replace(temporary, destination)


def safe_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._") or "song"


def job_id_for(input_path: Path) -> str:
    return f"{safe_name(input_path.stem)}-{sha256_file(input_path)[:10]}"


def is_candidate(path: Path) -> bool:
    return (path.is_file() and not path.name.startswith(".") and path.suffix.lower() in SUPPORTED_INPUTS
            and path.suffix.lower() not in IGNORED_SUFFIXES and not any(str(path).lower().endswith(s) for s in IGNORED_SUFFIXES))


def wait_until_stable(path: Path, seconds: float) -> bool:
    """Reject files still being copied; no hash or claim occurs before this check."""
    if seconds < 2 or seconds > 5:
        raise ValueError("stable_seconds must be between 2 and 5")
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


def ffmpeg_path(config: dict[str, Any], separation_python: Path) -> Path:
    configured = str(config.get("ffmpeg", "auto"))
    if configured.lower() != "auto":
        path = Path(configured)
        if path.is_file():
            return path
        found = shutil.which(configured)
        if found:
            return Path(found)
        raise RuntimeError(f"configured ffmpeg not found: {configured}")
    code = "import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())"
    return Path(subprocess.check_output([str(separation_python), "-c", code], text=True).strip())


def newest_stem(directory: Path, choices: tuple[str, ...]) -> Path:
    candidates = [item for item in directory.rglob("*.wav") if any(token in item.name.lower() for token in choices)]
    if not candidates:
        raise RuntimeError(f"pymss did not create one of {choices} under {directory}")
    return max(candidates, key=lambda item: item.stat().st_mtime_ns)


def pymss_infer(cli: Path, model: str, decoded: Path, results: Path, model_dir: Path,
                log: Path, *, tta: bool, download_source: str) -> Path:
    model_output = results / safe_name(model)
    command = [str(cli), "infer", model, "--model-dir", str(model_dir), "--download", "-i", str(decoded),
               "-o", str(model_output), "--save-as-folder", "--device", "cuda", "--format", "wav"]
    if download_source:
        command.extend(["--source", download_source])
    if tta:
        command.append("--tta")
    run(command, log=log)
    return model_output


def ensemble(cli: Path, inputs: list[Path], output: Path, algorithm: str, log: Path) -> None:
    if len(inputs) == 1:
        atomic_copy(inputs[0], output, Path(cli).parent / "python.exe")
        return
    run([str(cli), "ensemble", *map(str, inputs), "--algorithm", algorithm, "-o", str(output)], log=log)


def release_cuda(separation_python: Path, log: Path) -> None:
    # Every separator runs in a child process. This explicit second process also
    # clears the CUDA allocator before the stable Seed-VC process starts.
    run([str(separation_python), "-c", "import gc,torch; gc.collect(); torch.cuda.empty_cache()"], log=log)


def normalize_harmony_config(cfg: dict[str, Any], mode_override: str | None = None) -> dict[str, Any]:
    """Return an explicit V2.1 policy without changing legacy V2 defaults."""
    raw = cfg.get("harmony")
    if raw is None:
        return {"enabled": False, "mode": "legacy", "separator_model": None,
                "backing_gain_offset_db": 0.0}
    if not isinstance(raw, dict):
        raise ValueError("harmony must be an object when configured")
    enabled = bool(raw.get("enabled", False))
    mode = mode_override or str(raw.get("mode", "preserve")).lower()
    if mode not in HARMONY_MODES:
        raise ValueError("harmony.mode must be preserve or remove (convert is reserved for a future release)")
    offset = float(raw.get("backing_gain_offset_db", -3.0))
    if not -12.0 <= offset <= 3.0:
        raise ValueError("harmony.backing_gain_offset_db must be between -12 and +3 dB")
    model = raw.get("separator_model")
    if enabled and not isinstance(model, str):
        raise ValueError("harmony.separator_model is required when harmony.enabled is true")
    result = {"enabled": enabled, "mode": mode, "separator_model": model,
              "backing_gain_offset_db": offset}
    for key in ("lead_stem_tokens", "backing_stem_tokens"):
        if key in raw:
            value = raw[key]
            if not isinstance(value, list) or not value or not all(isinstance(item, str) for item in value):
                raise ValueError(f"harmony.{key} must be a non-empty string list")
            result[key] = [item.lower() for item in value]
    return result


def harmony_stem_tokens(harmony: dict[str, Any]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Use the catalog-proven UVR-BVE mapping unless a model supplies explicit tokens."""
    lead = tuple(harmony.get("lead_stem_tokens", ()))
    backing = tuple(harmony.get("backing_stem_tokens", ()))
    if lead and backing:
        return lead, backing
    model = str(harmony["separator_model"]).lower()
    if "bve" in model:
        # UVR-BVE emits its main (lead) residual as "Instrumental" and its
        # backing-vocal extract as "Vocals" even when the input is vocals only.
        return ("instrument",), ("vocal",)
    raise ValueError("harmony model requires lead_stem_tokens and backing_stem_tokens")


def latest_matching_stem(directory: Path, tokens: tuple[str, ...], *, exclude: Path | None = None) -> Path:
    candidates = [item for item in directory.rglob("*.wav")
                  if any(token in item.name.lower() for token in tokens) and item != exclude]
    if not candidates:
        raise RuntimeError(f"harmony separator did not create a stem matching {tokens} under {directory}")
    return max(candidates, key=lambda item: item.stat().st_mtime_ns)


def validate_backing_audio(python: Path, path: Path, *, expected_duration: float) -> tuple[dict[str, Any], bool]:
    """Validate a backing stem while allowing a genuinely empty arrangement."""
    if not path.is_file() or path.stat().st_size == 0:
        raise RuntimeError(f"missing or empty backing audio: {path}")
    metrics = audio_metrics(python, path)
    if not metrics["finite"] or metrics["duration"] <= 0:
        raise RuntimeError(f"invalid/non-finite backing audio: {path}")
    if metrics["sample_rate"] != SAMPLE_RATE:
        raise RuntimeError(f"unexpected sample rate {metrics['sample_rate']} for {path}")
    if abs(float(metrics["duration"]) - expected_duration) > 0.25:
        raise RuntimeError(f"duration drift exceeds 250 ms for backing stem {path}")
    if metrics["peak"] > 1.25:
        raise RuntimeError(f"catastrophic clipping in backing stem {path}")
    return metrics, bool(float(metrics["rms"]) < 1e-6)


def atomic_copy_backing(source: Path, destination: Path, python: Path, *, duration: float) -> tuple[dict[str, Any], bool]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".tmp")
    shutil.copyfile(source, temporary)
    metrics, no_backing = validate_backing_audio(python, temporary, expected_duration=duration)
    os.replace(temporary, destination)
    return metrics, no_backing


def harmony_separation_stage(cfg: dict[str, Any], paths: dict[str, Path], source_duration: float,
                             harmony: dict[str, Any], log: Path) -> dict[str, Any]:
    """Split an already isolated vocal stem into lead and preserved backing tracks."""
    sep = cfg["separation"]
    python = Path(sep["python"])
    cli = Path(sep.get("cli") or python.parent / "pymss.exe")
    model_dir = Path(sep["model_dir"])
    lead_tokens, backing_tokens = harmony_stem_tokens(harmony)
    results = paths["work"] / "harmony_separation"
    output = pymss_infer(cli, str(harmony["separator_model"]), paths["stems"] / "vocals_for_seedvc.wav",
                         results, model_dir, log, tta=False, download_source=str(sep.get("download_source", "")))
    lead_source = latest_matching_stem(output, lead_tokens)
    backing_source = latest_matching_stem(output, backing_tokens, exclude=lead_source)
    lead = paths["stems"] / "lead_vocals.wav"
    backing = paths["stems"] / "backing_vocals.wav"
    atomic_copy(lead_source, lead, python, duration=source_duration)
    backing_metrics, no_backing = atomic_copy_backing(backing_source, backing, python, duration=source_duration)
    lead_metrics = validate_audio(python, lead, expected_duration=source_duration)
    release_cuda(python, log)
    return {"lead_metrics": lead_metrics, "backing_metrics": backing_metrics,
            "no_backing_detected": no_backing, "engine": "pymss",
            "model": str(harmony["separator_model"])}


def load_config(config_path: Path) -> tuple[dict[str, Any], Path]:
    data = json.loads(config_path.read_text(encoding="utf-8"))
    base = config_path.parent
    for key in ("runtime_root", "incoming_dir", "processing_dir", "archive_dir", "work_dir", "output_dir", "failed_dir", "runtime_info_dir"):
        if key in data:
            data[key] = str(resolved(data[key], base))
    separation = data["separation"]
    for key in ("python", "model_dir", "cli"):
        if key in separation and separation[key] not in (None, ""):
            separation[key] = str(resolved(separation[key], base))
    seed = data["seed_vc"]
    for key in ("runtime_config", "target_reference", "wrapper"):
        if key in seed:
            seed[key] = str(resolved(seed[key], base))
    return data, base


def validate_managed_volumes(cfg: dict[str, Any]) -> None:
    """The inbox protocol relies on same-volume os.replace ownership claims."""
    keys = ("incoming_dir", "processing_dir", "archive_dir", "failed_dir")
    drives = {key: Path(cfg[key]).resolve().drive.casefold() for key in keys}
    if len(set(drives.values())) != 1:
        details = ", ".join(f"{key}={drive or '<none>'}" for key, drive in drives.items())
        raise ValueError(f"managed Singing directories must share one volume for atomic claims: {details}")


def unique_destination(directory: Path, filename: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    candidate = directory / filename
    stem, suffix = candidate.stem, candidate.suffix
    number = 2
    while candidate.exists():
        candidate = directory / f"{stem}-{number}{suffix}"
        number += 1
    return candidate


def lifecycle_output(cfg: dict[str, Any], job_id: str) -> Path:
    return Path(cfg["output_dir"]) / job_id


def claim_from_inbox(cfg: dict[str, Any], history: JobHistory, incoming: Path, *, force: bool) -> tuple[str, Path, str, int] | None:
    """Hash a stable inbox file, then atomically move it to its processing owner."""
    digest = sha256_file(incoming)
    canonical = history.successful_for_sha(digest)
    attempt_number = history.next_attempt(digest)
    base_id = f"{safe_name(incoming.stem)}-{digest[:10]}"
    job_id = base_id if attempt_number == 1 else f"{base_id}-attempt{attempt_number}"
    attempt_id = f"{job_id}:attempt{attempt_number}"
    if canonical and not force:
        destination = unique_destination(Path(cfg["archive_dir"]) / "duplicates", incoming.name)
        os.replace(incoming, destination)
        history.create(job_id=job_id, attempt_id=attempt_id, sha256=digest, original_filename=incoming.name,
                       processing_path=destination, force=False, pipeline_version=PIPELINE_VERSION,
                       state="success", stage="SKIP_DUPLICATE", canonical_job_id=str(canonical["job_id"]))
        history.update(job_id, state="success", stage="SKIP_DUPLICATE", archive_path=destination,
                       output_dir=Path(canonical["output_dir"]) if canonical["output_dir"] else None)
        write_json(destination.with_suffix(destination.suffix + ".duplicate.json"), {
            "job_id": job_id, "sha256": digest, "stage": "SKIP_DUPLICATE",
            "canonical_job_id": canonical["job_id"], "canonical_output_dir": canonical["output_dir"],
        })
        print(f"[SKIP_DUPLICATE] {incoming.name} -> {destination}")
        return None
    processing_dir = Path(cfg["processing_dir"]) / job_id
    processing_dir.mkdir(parents=True, exist_ok=False)
    processing = processing_dir / incoming.name
    # Same-volume os.replace is an atomic ownership claim. No source copy is made.
    os.replace(incoming, processing)
    history.create(job_id=job_id, attempt_id=attempt_id, sha256=digest, original_filename=incoming.name,
                   processing_path=processing, force=force, pipeline_version=PIPELINE_VERSION)
    return job_id, processing, digest, attempt_number


def archive_success(cfg: dict[str, Any], source: Path, job_id: str) -> Path:
    destination = unique_destination(Path(cfg["archive_dir"]), source.name)
    os.replace(source, destination)
    processing_parent = source.parent
    try:
        processing_parent.rmdir()
    except OSError:
        pass
    return destination


def archive_failure(cfg: dict[str, Any], source: Path, job_id: str) -> Path:
    destination = Path(cfg["failed_dir"]) / job_id / "source" / source.name
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.exists():
        os.replace(source, destination)
    try:
        source.parent.rmdir()
    except OSError:
        pass
    return destination


def process_claimed(cfg: dict[str, Any], history: JobHistory, job_id: str, source: Path, *, resume: bool,
                    skip_mix: bool, separation_only: bool, keep_work: bool,
                    harmony_mode_override: str | None = None, skip_harmony_separation: bool = False) -> bool:
    output = lifecycle_output(cfg, job_id)
    history.update(job_id, state="running", stage="PIPELINE")
    succeeded = process_input(cfg, source, resume=resume, skip_mix=skip_mix,
                              separation_only=separation_only, keep_work=True, job_id=job_id,
                              output_override=output, harmony_mode_override=harmony_mode_override,
                              skip_harmony_separation=skip_harmony_separation)
    if succeeded and not separation_only and not skip_mix:
        archived = archive_success(cfg, source, job_id)
        history.update(job_id, state="success", stage="SUCCESS", archive_path=archived, output_dir=output)
        if not keep_work:
            shutil.rmtree(Path(cfg["work_dir"]) / job_id, ignore_errors=True)
        return True
    if succeeded:
        history.update(job_id, state="partial", stage="SEPARATION_COMPLETE" if separation_only else "SEED_VC_COMPLETE",
                       output_dir=output)
        return True
    archived = archive_failure(cfg, source, job_id)
    history.update(job_id, state="failed", stage="FAILED", error="pipeline failed; see job manifest", archive_path=archived,
                   output_dir=output)
    return False


def resume_jobs(cfg: dict[str, Any], history: JobHistory, *, skip_mix: bool, separation_only: bool,
                keep_work: bool, harmony_mode_override: str | None = None,
                skip_harmony_separation: bool = False) -> list[bool]:
    results: list[bool] = []
    options: dict[str, Any] = {}
    if harmony_mode_override is not None:
        options["harmony_mode_override"] = harmony_mode_override
    if skip_harmony_separation:
        options["skip_harmony_separation"] = True
    for row in history.resumable():
        if row["state"] == "partial":
            source = Path(row["processing_path"])
            work = Path(cfg["work_dir"]) / row["job_id"]
            if not source.is_file() or not work.exists():
                history.update(row["job_id"], state="recoverable", stage="RECOVERY_REQUIRED",
                               error="recoverable partial-job mismatch: processing source or work is missing")
                results.append(False)
                continue
            results.append(process_claimed(cfg, history, row["job_id"], source, resume=True,
                                           skip_mix=skip_mix, separation_only=separation_only,
                                           keep_work=keep_work, **options))
            continue
        failed_job = Path(cfg["failed_dir"]) / row["job_id"]
        source = failed_job / "source" / row["original_filename"]
        if not source.is_file():
            history.update(row["job_id"], state="recoverable", stage="RECOVERY_REQUIRED",
                           error="recoverable state/files mismatch: failed source is missing")
            results.append(False)
            continue
        work = Path(cfg["work_dir"]) / row["job_id"]
        if work.exists():
            history.update(row["job_id"], state="recoverable", stage="RECOVERY_REQUIRED",
                           error="recoverable state/files mismatch: work directory already exists")
            results.append(False)
            continue
        work.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(failed_job), str(work))
        results.append(process_claimed(cfg, history, row["job_id"], work / "source" / row["original_filename"],
                                       resume=True, skip_mix=skip_mix, separation_only=separation_only,
                                       keep_work=keep_work, **options))
    return results


def separation_stage(cfg: dict[str, Any], paths: dict[str, Path], source_duration: float,
                     log: Path, warnings: list[str]) -> dict[str, Any]:
    sep = cfg["separation"]
    python = Path(sep["python"])
    cli = Path(sep.get("cli") or python.parent / "pymss.exe")
    if not python.is_file() or not cli.is_file():
        raise RuntimeError("isolated pymss Python/CLI is unavailable")
    model_dir = Path(sep["model_dir"])
    tta = bool(sep.get("tta", False))
    results = paths["work"] / "separation"
    vocal_files: list[Path] = []
    for model in sep["vocal_models"]:
        stem_dir = pymss_infer(cli, model, paths["decoded"], results, model_dir, log, tta=tta,
                                download_source=str(sep.get("download_source", "")))
        vocal_files.append(newest_stem(stem_dir, ("vocal",)))
    raw = paths["stems"] / "vocals_raw.wav"
    ensemble(cli, vocal_files, raw, sep.get("vocal_ensemble", "avg_fft"), log)
    raw_metrics = validate_audio(python, raw, expected_duration=source_duration)

    cleanup = sep.get("cleanup_mode", "light")
    seed_input = paths["stems"] / "vocals_for_seedvc.wav"
    if cleanup == "off":
        atomic_copy(raw, seed_input, python, duration=source_duration)
    else:
        cleanup_model = sep.get("cleanup_model")
        if cleanup_model:
            clean_dir = pymss_infer(cli, cleanup_model, raw, results / "cleanup", model_dir, log, tta=False,
                                     download_source=str(sep.get("download_source", "")))
            clean_source = newest_stem(clean_dir, ("dry", "noreverb", "no echo", "vocal"))
            clean = paths["stems"] / "vocals_clean.wav"
            atomic_copy(clean_source, clean, python, duration=source_duration)
            atomic_copy(clean, seed_input, python, duration=source_duration)
        else:
            warnings.append("cleanup_mode=light: no cleanup model configured; raw vocal retained to protect harmonics")
            atomic_copy(raw, seed_input, python, duration=source_duration)

    instrumental_files: list[Path] = []
    for model in sep["instrumental_models"]:
        stem_dir = pymss_infer(cli, model, paths["decoded"], results / "instrumental", model_dir, log, tta=tta,
                                download_source=str(sep.get("download_source", "")))
        instrumental_files.append(newest_stem(stem_dir, ("instrument", "other")))
    instrumental = paths["stems"] / "instrumental_clean.wav"
    ensemble(cli, instrumental_files, instrumental, sep.get("instrumental_ensemble", "avg_wave"), log)
    instrumental_metrics = validate_audio(python, instrumental, expected_duration=source_duration)
    release_cuda(python, log)
    return {"vocal": raw_metrics, "instrumental": instrumental_metrics}


def seed_vc_stage(cfg: dict[str, Any], paths: dict[str, Path], source_duration: float,
                  log: Path, *, input_stem: Path | None = None,
                  output_name: str = "yogurt_vocal.wav") -> dict[str, Any]:
    seed = cfg["seed_vc"]
    separation_python = Path(cfg["separation"]["python"])
    runtime_config = Path(seed["runtime_config"])
    target = Path(seed["target_reference"])
    wrapper = Path(seed["wrapper"])
    if not runtime_config.is_file() or not target.is_file() or not wrapper.is_file():
        raise RuntimeError("Seed-VC runtime config, target reference, or wrapper is missing")
    input_stem = input_stem or paths["stems"] / "vocals_for_seedvc.wav"
    source = paths["seed_input"] / f"source_{input_stem.stem}.wav"
    atomic_copy(input_stem, source, separation_python, duration=source_duration)
    # Seed-VC V1 can produce a final mel fragment shorter than BigVGAN's seven
    # sample kernel. Keep the verified handoff untouched and pad only its
    # private inference copy; final mixing remains clipped to the original
    # timeline below.
    tail_pad = float(seed.get("tail_pad_seconds", 5.0))
    inference_source = source
    if tail_pad > 0:
        inference_source = paths["seed_input"] / "source_vocals_seedvc_padded.wav"
        run([str(ffmpeg_path(cfg, separation_python)), "-y", "-i", str(source), "-af", f"apad=pad_dur={tail_pad}",
             "-ar", str(SAMPLE_RATE), "-ac", "2", "-c:a", "pcm_s24le", str(inference_source)], log=log)
        validate_audio(separation_python, inference_source, expected_duration=source_duration + tail_pad)
    runtime = json.loads(runtime_config.read_text(encoding="utf-8"))
    seed_output = paths["seed_output"]
    seed_output.mkdir(parents=True, exist_ok=True)
    runtime["output_dir"] = str(seed_output)
    job_runtime_config = paths["work"] / "seed_vc_runtime.json"
    json_dump(job_runtime_config, runtime)
    started = time.perf_counter()
    run([sys.executable, str(wrapper), "--config", str(job_runtime_config), "--source", str(inference_source), "--target", str(target),
         "--diffusion-steps", str(seed.get("diffusion_steps", 30))], log=log)
    produced = [item for item in seed_output.rglob("*.wav") if item.stat().st_mtime >= started - 5]
    if not produced:
        raise RuntimeError("Seed-VC completed without a WAV output")
    actual = max(produced, key=lambda item: item.stat().st_mtime_ns)
    standard = seed_output / output_name
    # V1 Seed-VC intentionally emits 22.05 kHz mono FLOAT WAV.  It is retained
    # losslessly here and aligned to 44.1 kHz stereo only by the final mixer.
    atomic_copy(actual, standard, separation_python, duration=source_duration + tail_pad, expected_rate=None)
    result = validate_audio(separation_python, standard, expected_duration=source_duration + tail_pad, expected_rate=None)
    result["tail_pad_seconds"] = tail_pad
    return result


def mix_stage(cfg: dict[str, Any], paths: dict[str, Path], source_duration: float, log: Path,
              *, harmony: dict[str, Any] | None = None, no_backing_detected: bool = False) -> dict[str, Any]:
    sep_python = Path(cfg["separation"]["python"])
    ffmpeg = ffmpeg_path(cfg, sep_python)
    harmony = harmony or {"enabled": False, "mode": "legacy", "backing_gain_offset_db": 0.0}
    harmony_enabled = bool(harmony["enabled"])
    reference_stem = paths["stems"] / ("lead_vocals.wav" if harmony_enabled else "vocals_raw.wav")
    yogurt_path = paths["seed_output"] / ("yogurt_lead.wav" if harmony_enabled else "yogurt_vocal.wav")
    original_vocal = audio_metrics(sep_python, reference_stem)
    yogurt = audio_metrics(sep_python, yogurt_path)
    gain = 0.0
    if cfg["mix"].get("auto_gain", True):
        import math
        gain = 20 * math.log10(max(float(original_vocal["rms"]), 1e-9) / max(float(yogurt["rms"]), 1e-9))
        maximum = float(cfg["mix"].get("max_gain_db", 6))
        gain = max(-maximum, min(maximum, gain))
    ceiling = 10 ** (float(cfg["mix"].get("limiter_ceiling_db", -1)) / 20.0)
    final_dir = paths["final"]
    final_dir.mkdir(parents=True, exist_ok=True)
    wav = final_dir / "yogurt_mix.wav"
    command = [str(ffmpeg), "-y", "-i", str(paths["mix"] / "instrumental.wav"), "-i", str(yogurt_path)]
    include_backing = harmony_enabled and harmony["mode"] == "preserve" and not no_backing_detected
    if include_backing:
        command.extend(["-i", str(paths["stems"] / "backing_vocals.wav")])
        filter_graph = (f"[1:a]apad,volume={gain:.4f}dB[lead];[2:a]apad,volume={float(harmony['backing_gain_offset_db']):.4f}dB[back];"
                        f"[0:a][lead][back]amix=inputs=3:duration=first:normalize=0,alimiter=limit={ceiling:.7f}[m]")
    else:
        filter_graph = f"[1:a]apad,volume={gain:.4f}dB[lead];[0:a][lead]amix=inputs=2:duration=first:normalize=0,alimiter=limit={ceiling:.7f}[m]"
    command.extend(["-filter_complex", filter_graph, "-map", "[m]", "-t", f"{source_duration:.6f}", "-ar", str(SAMPLE_RATE), "-ac", "2",
                    "-c:a", "pcm_s24le", str(wav)])
    run(command, log=log)
    metrics = validate_audio(sep_python, wav, expected_duration=source_duration)
    mp3 = final_dir / "yogurt_mix.mp3"
    run([str(ffmpeg), "-y", "-i", str(wav), "-c:a", "libmp3lame", "-b:a", "320k", str(mp3)], log=log)
    if not mp3.is_file() or mp3.stat().st_size < 1024:
        raise RuntimeError("MP3 encode failed")
    metrics["auto_gain_db"] = gain
    metrics["backing_included"] = include_backing
    if harmony_enabled:
        metrics["backing_gain_offset_db"] = float(harmony["backing_gain_offset_db"])
    return metrics


def process_input(cfg: dict[str, Any], input_path: Path, *, resume: bool, skip_mix: bool,
                  separation_only: bool, keep_work: bool, harmony_mode_override: str | None = None,
                  skip_harmony_separation: bool = False, job_id: str | None = None,
                  output_override: Path | None = None) -> bool:
    separation_python = Path(cfg["separation"]["python"])
    harmony = normalize_harmony_config(cfg, harmony_mode_override)
    job_id = job_id or job_id_for(input_path)
    work = Path(cfg["work_dir"]) / job_id
    failed = Path(cfg["failed_dir"]) / job_id
    # A failed job is moved wholesale for diagnosis. --resume returns that same
    # job directory to work so verified stages remain available.
    if resume and not work.exists() and failed.exists():
        work.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(failed), str(work))
    output = output_override or (Path(cfg["output_dir"]) / safe_name(input_path.stem))
    primary_manifest = output / "job_manifest.json"
    if output.exists() and (not primary_manifest.is_file() or json.loads(primary_manifest.read_text(encoding="utf-8")).get("job_id") != job_id):
        output = Path(cfg["output_dir"]) / job_id
    paths = {"work": work, "source": output / "source", "stems": output / "stems", "vocal": output / "vocal",
             "final": output / "final", "decoded": work / "decoded.wav", "seed_input": work / "seed_vc_input",
             "seed_output": work / "seed_vc_output", "mix": work / "mix"}
    manifest_path = output / "job_manifest.json"
    manifest: dict[str, Any] = {"pipeline_version": PIPELINE_VERSION, "job_id": job_id, "input_file": str(input_path),
        "input_codec": input_path.suffix.lower().lstrip("."), "status": "running", "started_at": now(), "warnings": [],
        "separation_engine": "pymss", "pymss_version": "unknown", "cuda_device": "unknown",
        "vocal_models": cfg["separation"]["vocal_models"], "instrumental_models": cfg["separation"]["instrumental_models"],
        "vocal_ensemble": cfg["separation"].get("vocal_ensemble"), "instrumental_ensemble": cfg["separation"].get("instrumental_ensemble"),
        "cleanup_mode": cfg["separation"].get("cleanup_mode", "light"),
        "harmony_separation_enabled": bool(harmony["enabled"]), "harmony_mode": harmony["mode"]}
    if harmony["enabled"]:
        manifest.update({"harmony_separator_engine": "pymss", "harmony_separator_model": harmony["separator_model"],
                         "backing_gain_offset_db": harmony["backing_gain_offset_db"],
                         "seed_vc_input_type": "lead_only"})
    if resume and manifest_path.is_file():
        manifest.update(json.loads(manifest_path.read_text(encoding="utf-8")))
        manifest["status"] = "running"
        # Configuration is authoritative on a resumed job; retain timing and
        # diagnostics from the old manifest but never silently reuse its model set.
        manifest.update({"vocal_models": cfg["separation"]["vocal_models"],
                         "instrumental_models": cfg["separation"]["instrumental_models"],
                         "vocal_ensemble": cfg["separation"].get("vocal_ensemble"),
                         "instrumental_ensemble": cfg["separation"].get("instrumental_ensemble"),
                         "cleanup_mode": cfg["separation"].get("cleanup_mode", "light"),
                         "harmony_separation_enabled": bool(harmony["enabled"]),
                         "harmony_mode": harmony["mode"]})
        if harmony["enabled"]:
            manifest.update({"harmony_separator_engine": "pymss",
                             "harmony_separator_model": harmony["separator_model"],
                             "backing_gain_offset_db": harmony["backing_gain_offset_db"],
                             "seed_vc_input_type": "lead_only"})
        manifest.pop("error", None)
        manifest["resumed_at"] = now()
    log = work / "logs" / "pipeline.log"
    started = time.perf_counter()
    try:
        for path in (work, paths["source"], paths["stems"], paths["vocal"], paths["final"], paths["seed_input"], paths["seed_output"], paths["mix"]):
            path.mkdir(parents=True, exist_ok=True)
        shutil.copy2(input_path, paths["source"] / f"original{input_path.suffix.lower()}")
        probe = subprocess.check_output([str(separation_python), "-c", "import importlib.metadata,torch; print(importlib.metadata.version('pymss')); print(torch.cuda.get_device_name(0))"], text=True).splitlines()
        manifest["pymss_version"], manifest["cuda_device"] = probe[0], probe[1]
        ffmpeg = ffmpeg_path(cfg, separation_python)
        if not (resume and paths["decoded"].is_file()):
            run([str(ffmpeg), "-y", "-i", str(input_path), "-vn", "-ar", str(SAMPLE_RATE), "-ac", "2", "-c:a", "pcm_s24le", str(paths["decoded"])], log=log)
        source_metrics = validate_audio(separation_python, paths["decoded"])
        manifest["input_duration"] = source_metrics["duration"]
        separation_started = time.perf_counter()
        separation_reused = resume and (paths["stems"] / "vocals_for_seedvc.wav").is_file() and (paths["stems"] / "instrumental_clean.wav").is_file()
        if not separation_reused:
            manifest["separation_metrics"] = separation_stage(cfg, paths, float(source_metrics["duration"]), log, manifest["warnings"])
            manifest["separation_latency_seconds"] = round(time.perf_counter() - separation_started, 3)
        atomic_copy(paths["stems"] / "instrumental_clean.wav", paths["mix"] / "instrumental.wav", separation_python, duration=float(source_metrics["duration"]))
        if separation_only:
            manifest["status"] = "separation_complete"
            manifest["stage"] = "VOCAL_SEPARATION_COMPLETE"
        else:
            no_backing_detected = False
            if harmony["enabled"]:
                lead = paths["stems"] / "lead_vocals.wav"
                backing = paths["stems"] / "backing_vocals.wav"
                harmony_reused = resume and lead.is_file() and backing.is_file()
                manifest["stage"] = "HARMONY_SEPARATION_RUNNING"
                harmony_started = time.perf_counter()
                if harmony_reused:
                    manifest["lead_metrics"] = validate_audio(separation_python, lead, expected_duration=float(source_metrics["duration"]))
                    manifest["backing_metrics"], no_backing_detected = validate_backing_audio(
                        separation_python, backing, expected_duration=float(source_metrics["duration"]))
                    manifest.setdefault("harmony_separation_latency", 0.0)
                elif skip_harmony_separation:
                    raise RuntimeError("--skip-harmony-separation cannot bypass a fresh harmony-enabled production job")
                else:
                    harmony_result = harmony_separation_stage(cfg, paths, float(source_metrics["duration"]), harmony, log)
                    manifest["lead_metrics"] = harmony_result["lead_metrics"]
                    manifest["backing_metrics"] = harmony_result["backing_metrics"]
                    no_backing_detected = bool(harmony_result["no_backing_detected"])
                    manifest["harmony_separation_latency"] = round(time.perf_counter() - harmony_started, 3)
                manifest["no_backing_detected"] = no_backing_detected
                manifest["stage"] = "HARMONY_SEPARATION_COMPLETE"
            seed_started = time.perf_counter()
            seed_name = "yogurt_lead.wav" if harmony["enabled"] else "yogurt_vocal.wav"
            seed_reused = resume and (paths["seed_output"] / seed_name).is_file()
            if not seed_reused:
                manifest["seed_vc_metrics"] = seed_vc_stage(
                    cfg, paths, float(source_metrics["duration"]), log,
                    input_stem=(paths["stems"] / "lead_vocals.wav") if harmony["enabled"] else None,
                    output_name=seed_name)
                manifest["seed_vc_latency_seconds"] = round(time.perf_counter() - seed_started, 3)
            atomic_copy(paths["seed_output"] / seed_name, paths["vocal"] / seed_name, separation_python,
                        expected_rate=None)
            if harmony["enabled"]:
                atomic_copy(paths["vocal"] / "yogurt_lead.wav", paths["vocal"] / "yogurt_vocal.wav", separation_python,
                            expected_rate=None)
            if skip_mix:
                manifest["status"] = "seed_vc_complete"
                manifest["stage"] = "SEED_VC_COMPLETE"
            else:
                mix_started = time.perf_counter()
                manifest["mix_metrics"] = mix_stage(cfg, paths, float(source_metrics["duration"]), log,
                                                     harmony=harmony, no_backing_detected=no_backing_detected)
                manifest["mix_latency_seconds"] = round(time.perf_counter() - mix_started, 3)
                manifest["status"] = "success"
                manifest["stage"] = "MIX_COMPLETE"
        manifest["output_paths"] = {key: str(value) for key, value in paths.items() if key in {"source", "stems", "vocal", "final"}}
        manifest["total_latency_seconds"] = round(sum(float(manifest.get(key, 0)) for key in
                                                       ("separation_latency_seconds", "harmony_separation_latency",
                                                        "seed_vc_latency_seconds", "mix_latency_seconds")), 3)
        manifest["finished_at"] = now()
        json_dump(manifest_path, manifest)
        if not keep_work and manifest["status"] == "success":
            shutil.rmtree(work, ignore_errors=True)
        print(f"[{manifest['status'].upper()}] {input_path.name} -> {output}")
        return True
    except Exception as exc:
        manifest["status"] = "failed"
        if harmony["enabled"] and str(manifest.get("stage", "")).startswith("HARMONY_"):
            manifest["stage"] = "HARMONY_SEPARATION_FAILED"
        manifest["error"] = str(exc)
        manifest["total_latency_seconds"] = round(time.perf_counter() - started, 3)
        manifest["finished_at"] = now()
        json_dump(manifest_path, manifest)
        failed.parent.mkdir(parents=True, exist_ok=True)
        if work.exists():
            if failed.exists():
                shutil.rmtree(failed)
            shutil.move(str(work), str(failed))
        json_dump(failed / "job_manifest.json", manifest)
        print(f"[FAILED] {input_path.name}: {exc}", file=sys.stderr)
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--input", type=Path, help="process one input instead of scanning incoming")
    parser.add_argument("--quality", choices=["maximum"], default="maximum")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--force", action="store_true", help="create a new attempt even when SHA-256 already succeeded")
    parser.add_argument("--keep-work", action="store_true")
    parser.add_argument("--skip-mix", action="store_true")
    parser.add_argument("--separation-only", action="store_true")
    parser.add_argument("--harmony-mode", choices=sorted(HARMONY_MODES),
                        help="override a configured harmony preserve/remove mode")
    parser.add_argument("--skip-harmony-separation", action="store_true",
                        help="debug only; a fresh harmony-enabled job fails closed instead of using mixed vocals")
    args = parser.parse_args()
    cfg, _ = load_config(args.config.resolve())
    runtime_root = Path(cfg["runtime_root"])
    cfg.setdefault("processing_dir", str(runtime_root / "processing"))
    cfg.setdefault("archive_dir", str(runtime_root / "archive"))
    cfg.setdefault("runtime_info_dir", str(runtime_root / "runtime_info"))
    cfg.setdefault("stable_seconds", 2)
    for key in ("incoming_dir", "processing_dir", "archive_dir", "work_dir", "output_dir", "failed_dir", "runtime_info_dir"):
        Path(cfg[key]).mkdir(parents=True, exist_ok=True)
    validate_managed_volumes(cfg)
    if args.input:
        # Legacy one-file mode deliberately stays non-destructive for callers
        # outside the managed inbox. Production jobs use the inbox branch below.
        return 0 if process_input(cfg, args.input.resolve(), resume=args.resume, skip_mix=args.skip_mix,
                                   separation_only=args.separation_only, keep_work=args.keep_work,
                                   harmony_mode_override=args.harmony_mode,
                                   skip_harmony_separation=args.skip_harmony_separation) else 1
    try:
        with PipelineLock(Path(cfg["runtime_info_dir"]) / "singing_pipeline.lock"), \
                JobHistory(Path(cfg["runtime_info_dir"]) / "job_history.sqlite3") as history:
            recovery = history.recover_interrupted()
            for item in recovery:
                print(f"[RECOVERY_REQUIRED] {item['job_id']}: {item['error']}", file=sys.stderr)
            results: list[bool] = []
            if args.resume:
                results.extend(resume_jobs(cfg, history, skip_mix=args.skip_mix,
                                           separation_only=args.separation_only, keep_work=args.keep_work,
                                           harmony_mode_override=args.harmony_mode,
                                           skip_harmony_separation=args.skip_harmony_separation))
            candidates = sorted(Path(cfg["incoming_dir"]).iterdir())
            for path in candidates:
                if not is_candidate(path):
                    continue
                if not wait_until_stable(path, float(cfg["stable_seconds"])):
                    print(f"[WAITING_FOR_STABLE_FILE] {path.name}")
                    continue
                claim = claim_from_inbox(cfg, history, path, force=args.force)
                if claim is None:
                    continue
                job_id, source, _, _ = claim
                results.append(process_claimed(cfg, history, job_id, source, resume=False,
                                               skip_mix=args.skip_mix, separation_only=args.separation_only,
                                               keep_work=args.keep_work,
                                               harmony_mode_override=args.harmony_mode,
                                               skip_harmony_separation=args.skip_harmony_separation))
            if not results:
                print("No stable supported audio found.")
            return 0 if all(results) else 1
    except PipelineAlreadyRunning as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
