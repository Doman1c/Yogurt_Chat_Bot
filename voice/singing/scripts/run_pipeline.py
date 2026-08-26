"""Sequential, failure-isolated singing conversion pipeline.

This module deliberately orchestrates the existing ``run_seed_vc.py`` wrapper;
it never imports, modifies, or reimplements Seed-VC inference.
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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SUPPORTED_INPUTS = {".mp3", ".wav", ".flac", ".m4a"}
SAMPLE_RATE = 44_100
PIPELINE_VERSION = "2"


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
    stat = input_path.stat()
    digest = hashlib.sha256(f"{input_path.resolve()}:{stat.st_size}:{stat.st_mtime_ns}".encode()).hexdigest()[:10]
    return f"{safe_name(input_path.stem)}-{digest}"


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


def load_config(config_path: Path) -> tuple[dict[str, Any], Path]:
    data = json.loads(config_path.read_text(encoding="utf-8"))
    base = config_path.parent
    for key in ("runtime_root", "incoming_dir", "work_dir", "output_dir", "failed_dir"):
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


def separation_stage(cfg: dict[str, Any], paths: dict[str, Path], source_duration: float,
                     log: Path, warnings: list[str]) -> dict[str, Any]:
    sep = cfg["separation"]
    python = Path(sep["python"])
    cli = Path(sep.get("cli") or python.parent / "pymss.exe")
    if not python.is_file() or not cli.is_file():
        raise RuntimeError("isolated pymss Python/CLI is unavailable")
    model_dir = Path(sep["model_dir"])
    tta = bool(sep.get("tta", False))
    results = paths["work"] / "separation_results"
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
                  log: Path) -> dict[str, Any]:
    seed = cfg["seed_vc"]
    separation_python = Path(cfg["separation"]["python"])
    runtime_config = Path(seed["runtime_config"])
    target = Path(seed["target_reference"])
    wrapper = Path(seed["wrapper"])
    if not runtime_config.is_file() or not target.is_file() or not wrapper.is_file():
        raise RuntimeError("Seed-VC runtime config, target reference, or wrapper is missing")
    source = paths["seed_input"] / "source_vocals.wav"
    atomic_copy(paths["stems"] / "vocals_for_seedvc.wav", source, separation_python, duration=source_duration)
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
    standard = seed_output / "yogurt_vocal.wav"
    # V1 Seed-VC intentionally emits 22.05 kHz mono FLOAT WAV.  It is retained
    # losslessly here and aligned to 44.1 kHz stereo only by the final mixer.
    atomic_copy(actual, standard, separation_python, duration=source_duration + tail_pad, expected_rate=None)
    result = validate_audio(separation_python, standard, expected_duration=source_duration + tail_pad, expected_rate=None)
    result["tail_pad_seconds"] = tail_pad
    return result


def mix_stage(cfg: dict[str, Any], paths: dict[str, Path], source_duration: float, log: Path) -> dict[str, Any]:
    sep_python = Path(cfg["separation"]["python"])
    ffmpeg = ffmpeg_path(cfg, sep_python)
    original_vocal = audio_metrics(sep_python, paths["stems"] / "vocals_raw.wav")
    yogurt = audio_metrics(sep_python, paths["seed_output"] / "yogurt_vocal.wav")
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
    filter_graph = f"[1:a]apad,volume={gain:.4f}dB[v];[0:a][v]amix=inputs=2:duration=first:normalize=0,alimiter=limit={ceiling:.7f}[m]"
    run([str(ffmpeg), "-y", "-i", str(paths["mix"] / "instrumental.wav"), "-i", str(paths["seed_output"] / "yogurt_vocal.wav"),
         "-filter_complex", filter_graph, "-map", "[m]", "-t", f"{source_duration:.6f}", "-ar", str(SAMPLE_RATE), "-ac", "2",
         "-c:a", "pcm_s24le", str(wav)], log=log)
    metrics = validate_audio(sep_python, wav, expected_duration=source_duration)
    mp3 = final_dir / "yogurt_mix.mp3"
    run([str(ffmpeg), "-y", "-i", str(wav), "-c:a", "libmp3lame", "-b:a", "320k", str(mp3)], log=log)
    if not mp3.is_file() or mp3.stat().st_size < 1024:
        raise RuntimeError("MP3 encode failed")
    metrics["auto_gain_db"] = gain
    return metrics


def process_input(cfg: dict[str, Any], input_path: Path, *, resume: bool, skip_mix: bool,
                  separation_only: bool, keep_work: bool) -> bool:
    separation_python = Path(cfg["separation"]["python"])
    job_id = job_id_for(input_path)
    work = Path(cfg["work_dir"]) / job_id
    failed = Path(cfg["failed_dir"]) / job_id
    # A failed job is moved wholesale for diagnosis. --resume returns that same
    # job directory to work so verified stages remain available.
    if resume and not work.exists() and failed.exists():
        work.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(failed), str(work))
    output = Path(cfg["output_dir"]) / safe_name(input_path.stem)
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
        "cleanup_mode": cfg["separation"].get("cleanup_mode", "light")}
    if resume and manifest_path.is_file():
        manifest.update(json.loads(manifest_path.read_text(encoding="utf-8")))
        manifest["status"] = "running"
        # Configuration is authoritative on a resumed job; retain timing and
        # diagnostics from the old manifest but never silently reuse its model set.
        manifest.update({"vocal_models": cfg["separation"]["vocal_models"],
                         "instrumental_models": cfg["separation"]["instrumental_models"],
                         "vocal_ensemble": cfg["separation"].get("vocal_ensemble"),
                         "instrumental_ensemble": cfg["separation"].get("instrumental_ensemble"),
                         "cleanup_mode": cfg["separation"].get("cleanup_mode", "light")})
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
        else:
            seed_started = time.perf_counter()
            seed_reused = resume and (paths["seed_output"] / "yogurt_vocal.wav").is_file()
            if not seed_reused:
                manifest["seed_vc_metrics"] = seed_vc_stage(cfg, paths, float(source_metrics["duration"]), log)
                manifest["seed_vc_latency_seconds"] = round(time.perf_counter() - seed_started, 3)
            atomic_copy(paths["seed_output"] / "yogurt_vocal.wav", paths["vocal"] / "yogurt_vocal.wav", separation_python,
                        expected_rate=None)
            if skip_mix:
                manifest["status"] = "seed_vc_complete"
            else:
                mix_started = time.perf_counter()
                manifest["mix_metrics"] = mix_stage(cfg, paths, float(source_metrics["duration"]), log)
                manifest["mix_latency_seconds"] = round(time.perf_counter() - mix_started, 3)
                manifest["status"] = "success"
        manifest["output_paths"] = {key: str(value) for key, value in paths.items() if key in {"source", "stems", "vocal", "final"}}
        manifest["total_latency_seconds"] = round(sum(float(manifest.get(key, 0)) for key in
                                                       ("separation_latency_seconds", "seed_vc_latency_seconds", "mix_latency_seconds")), 3)
        manifest["finished_at"] = now()
        json_dump(manifest_path, manifest)
        if not keep_work and manifest["status"] == "success":
            shutil.rmtree(work, ignore_errors=True)
        print(f"[{manifest['status'].upper()}] {input_path.name} -> {output}")
        return True
    except Exception as exc:
        manifest["status"] = "failed"
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
    parser.add_argument("--keep-work", action="store_true")
    parser.add_argument("--skip-mix", action="store_true")
    parser.add_argument("--separation-only", action="store_true")
    args = parser.parse_args()
    cfg, _ = load_config(args.config.resolve())
    candidates = [args.input.resolve()] if args.input else sorted(Path(cfg["incoming_dir"]).glob("*"))
    inputs = [path for path in candidates if path.is_file() and path.suffix.lower() in SUPPORTED_INPUTS]
    if not inputs:
        print("No supported audio found.")
        return 0
    results = [process_input(cfg, path, resume=args.resume, skip_mix=args.skip_mix,
                             separation_only=args.separation_only, keep_work=args.keep_work) for path in inputs]
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
