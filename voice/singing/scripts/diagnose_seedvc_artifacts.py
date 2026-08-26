"""Create bounded, repeatable Seed-VC artifact diagnostics for one existing job.

This tool never calls source separation and never converts a whole song.  It
cuts requested windows from verified job artifacts, invokes the established
``run_seed_vc.py`` wrapper for each requested diffusion-step value, and writes
only diagnostic outputs beneath the job root.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf


def parse_ranges(value: str) -> list[tuple[float, float]]:
    """Parse ``start:end`` comma-separated windows and reject malformed input."""
    ranges: list[tuple[float, float]] = []
    for item in value.split(","):
        try:
            start_text, end_text = item.strip().split(":", 1)
            start, end = float(start_text), float(end_text)
        except ValueError as exc:
            raise argparse.ArgumentTypeError(
                f"invalid range {item!r}; use start:end,start:end"
            ) from exc
        if start < 0 or end <= start:
            raise argparse.ArgumentTypeError(f"invalid range {item!r}")
        ranges.append((start, end))
    if not ranges:
        raise argparse.ArgumentTypeError("at least one range is required")
    return ranges


def parse_steps(value: str) -> list[int]:
    try:
        steps = [int(item.strip()) for item in value.split(",")]
    except ValueError as exc:
        raise argparse.ArgumentTypeError("steps must be comma-separated integers") from exc
    if not steps or any(step < 1 or step > 100 for step in steps):
        raise argparse.ArgumentTypeError("each diffusion step must be in 1..100")
    return steps


def audio_info(path: Path) -> dict[str, Any]:
    info = sf.info(path)
    return {
        "basename": path.name,
        "duration_seconds": info.frames / info.samplerate,
        "sample_rate": info.samplerate,
        "channels": info.channels,
        "frames": info.frames,
        "format": info.format,
        "subtype": info.subtype,
    }


def cut_audio(source: Path, destination: Path, start: float, end: float) -> dict[str, Any]:
    """Frame-accurately cut audio without gain, normalization, or DSP."""
    info = sf.info(source)
    start_frame = int(round(start * info.samplerate))
    end_frame = int(round(end * info.samplerate))
    if end_frame > info.frames:
        raise ValueError(f"range {start}:{end} exceeds {source.name} duration")
    data, sample_rate = sf.read(source, start=start_frame, stop=end_frame, always_2d=True)
    sf.write(destination, data, sample_rate, subtype="FLOAT")
    return {"start_seconds": start, "end_seconds": end, "frames": len(data), "sample_rate": sample_rate}


def write_padded_copy(source: Path, destination: Path, tail_pad_seconds: float) -> None:
    data, sample_rate = sf.read(source, always_2d=True)
    padding = np.zeros((int(round(tail_pad_seconds * sample_rate)), data.shape[1]), dtype=data.dtype)
    sf.write(destination, np.concatenate((data, padding)), sample_rate, subtype="FLOAT")


def mel_frame_count(samples: int, n_fft: int, hop_length: int) -> int:
    """Mirror Seed-VC's reflected (n_fft-hop)/2 padding and center=False STFT."""
    padded_samples = samples + (n_fft - hop_length)
    return 1 + (padded_samples - n_fft) // hop_length


def calculate_boundaries(
    *, source_duration: float, sample_rate: int, reference_duration: float,
    n_fft: int, hop_length: int, max_context_seconds: int = 30,
    overlap_frames: int = 16, reference_limit_seconds: int = 25,
) -> dict[str, Any]:
    """Calculate the chunk traversal used by the pinned inference.py implementation."""
    max_context_frames = sample_rate // hop_length * max_context_seconds
    reference_used_seconds = min(reference_duration, reference_limit_seconds)
    reference_frames = mel_frame_count(int(reference_used_seconds * sample_rate), n_fft, hop_length)
    max_source_frames = max_context_frames - reference_frames
    if max_source_frames <= overlap_frames:
        raise ValueError("reference consumes all Seed-VC source context")
    source_frames = mel_frame_count(int(source_duration * sample_rate), n_fft, hop_length)
    advance_frames = max_source_frames - overlap_frames
    boundaries: list[float] = []
    processed_frames = 0
    while processed_frames + max_source_frames < source_frames:
        processed_frames += advance_frames
        boundaries.append(processed_frames * hop_length / sample_rate)
    return {
        "sample_rate": sample_rate,
        "n_fft": n_fft,
        "hop_length": hop_length,
        "max_context_seconds": max_context_seconds,
        "max_context_frames": max_context_frames,
        "reference_file_duration_seconds": reference_duration,
        "reference_used_seconds": reference_used_seconds,
        "reference_mel_frames": reference_frames,
        "max_source_mel_frames": max_source_frames,
        "estimated_source_chunk_seconds": max_source_frames * hop_length / sample_rate,
        "overlap_frames": overlap_frames,
        "overlap_seconds": overlap_frames * hop_length / sample_rate,
        "advance_frames": advance_frames,
        "advance_seconds": advance_frames * hop_length / sample_rate,
        "boundaries_seconds": boundaries,
    }


def nearest_boundary(center: float, boundaries: list[float]) -> dict[str, Any]:
    boundary = min(boundaries, key=lambda value: abs(value - center))
    distance = abs(boundary - center)
    return {
        "artifact_center_seconds": center,
        "nearest_boundary_seconds": boundary,
        "distance_seconds": distance,
        "proximity": "VERY_CLOSE" if distance < 1 else "CLOSE" if distance < 2 else "NOT_CLOSE",
    }


def basic_metrics(path: Path) -> dict[str, float]:
    data, _ = sf.read(path, always_2d=True)
    values = data.astype(np.float64, copy=False)
    return {"peak": float(np.max(np.abs(values))), "rms": float(math.sqrt(np.mean(np.square(values))))}


def concat_comparison(files: list[Path], destination: Path) -> None:
    """Join without level changes; all inputs must be compatible by design."""
    rendered: list[np.ndarray] = []
    sample_rate: int | None = None
    channels: int | None = None
    for path in files:
        data, rate = sf.read(path, always_2d=True)
        if sample_rate is None:
            sample_rate, channels = rate, data.shape[1]
        if rate != sample_rate or data.shape[1] != channels:
            raise ValueError(f"cannot concatenate incompatible audio: {path.name}")
        rendered.append(data)
        rendered.append(np.zeros((sample_rate, channels), dtype=data.dtype))
    sf.write(destination, np.concatenate(rendered[:-1]), sample_rate, subtype="FLOAT")


def f0_diagnostic(path: Path) -> dict[str, Any]:
    """Use existing librosa only; failures remain optional diagnostics, not blockers."""
    try:
        import librosa

        audio, rate = librosa.load(path, sr=None, mono=True)
        f0, voiced, _ = librosa.pyin(audio, fmin=librosa.note_to_hz("C2"), fmax=librosa.note_to_hz("C7"), sr=rate)
        voiced_f0 = np.where(voiced, f0, np.nan)
        cents = 1200 * np.diff(np.log2(voiced_f0))
        flagged = np.flatnonzero(np.isfinite(cents) & (np.abs(cents) > 500))
        octave_like = np.flatnonzero(np.isfinite(cents) & (np.abs(cents) > 900))
        return {
            "available": True,
            "voiced_fraction": float(np.mean(voiced)),
            "abrupt_jumps_over_500_cents": flagged.tolist(),
            "octave_like_jumps_over_900_cents": octave_like.tolist(),
            "hop_length": 512,
        }
    except Exception as exc:  # optional, and must never cause an environment change
        return {"available": False, "error": f"{type(exc).__name__}: {exc}"}


def run_wrapper(wrapper: Path, runtime: dict[str, Any], source: Path, target: Path, output_dir: Path, steps: int) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    runtime = dict(runtime)
    runtime["output_dir"] = str(output_dir)
    with tempfile.TemporaryDirectory(prefix="seedvc-diagnostic-") as temp_dir:
        runtime_path = Path(temp_dir) / "runtime.json"
        runtime_path.write_text(json.dumps(runtime), encoding="utf-8")
        command = [str(runtime["python"]), str(wrapper), "--config", str(runtime_path), "--source", str(source), "--target", str(target), "--diffusion-steps", str(steps)]
        completed = subprocess.run(command, check=False)
    if completed.returncode:
        raise RuntimeError(
            f"Seed-VC wrapper failed for {source.name} at {steps} steps "
            f"(return code {completed.returncode})"
        )
    produced = list(output_dir.glob("*.wav"))
    if not produced:
        raise RuntimeError(f"Seed-VC wrapper produced no WAV for {source.name} at {steps} steps")
    return max(produced, key=lambda item: item.stat().st_mtime_ns)


def trim_to_duration(source: Path, destination: Path, duration_seconds: float) -> None:
    data, rate = sf.read(source, always_2d=True)
    expected_frames = int(round(duration_seconds * rate))
    if len(data) < expected_frames:
        raise RuntimeError(f"Seed-VC output is shorter than requested window: {source.name}")
    sf.write(destination, data[:expected_frames], rate, subtype="FLOAT")


def render_report(manifest: dict[str, Any], boundaries: dict[str, Any], f0_results: dict[str, Any]) -> str:
    """Render evidence and listening instructions without judging audio quality."""
    future_context: list[str] = []
    for seconds in (6, 8, 10):
        frames = mel_frame_count(seconds * boundaries["sample_rate"], boundaries["n_fft"], boundaries["hop_length"])
        available = boundaries["max_context_frames"] - frames
        future_context.append(f"- {seconds}s reference: {available * boundaries['hop_length'] / boundaries['sample_rate']:.3f}s source context ({available} frames)")
    boundary_lines = ", ".join(f"{item:.3f}s" for item in boundaries["boundaries_seconds"])
    segment_lines: list[str] = []
    for segment in manifest["segments"]:
        near = segment["boundary_proximity"]
        segment_lines.append(
            f"- `{segment['label']}` ({segment['range']['start_seconds']:.3f}-{segment['range']['end_seconds']:.3f}s): "
            f"nearest boundary {near['nearest_boundary_seconds']:.3f}s; "
            f"distance {near['distance_seconds']:.3f}s — {near['proximity']}."
        )
    f0_lines: list[str] = []
    for label, files in f0_results.items():
        flagged = [name for name, result in files.items() if result.get("available") and result.get("abrupt_jumps_over_500_cents")]
        f0_lines.append(f"- `{label}`: >500-cent adjacent-frame events in {', '.join(flagged) if flagged else 'none detected'}.")
    return "\n".join([
        "# Seed-VC artifact diagnostic",
        "",
        "## Scope",
        "",
        "This run uses existing separated vocals and the existing Seed-VC wrapper only. It does not run source separation, alter the original song, alter the pinned Seed-VC checkout, or run a whole-song conversion.",
        "",
        "## Pinned-code context calculation",
        "",
        f"- Target reference: `{manifest['target_reference']['basename']}`; file duration {boundaries['reference_file_duration_seconds']:.3f}s. Pinned `inference.py` uses only the first {boundaries['reference_used_seconds']:.3f}s.",
        f"- Max context: {boundaries['max_context_seconds']}s / {boundaries['max_context_frames']} mel frames at {boundaries['sample_rate']} Hz with hop {boundaries['hop_length']}.",
        f"- Reference occupies {boundaries['reference_mel_frames']} mel frames; each source chunk can contain {boundaries['max_source_mel_frames']} frames ({boundaries['estimated_source_chunk_seconds']:.3f}s).",
        f"- Overlap/crossfade: {boundaries['overlap_frames']} frames ({boundaries['overlap_seconds']:.3f}s); advancing chunk starts by {boundaries['advance_frames']} frames ({boundaries['advance_seconds']:.3f}s).",
        f"- Theoretical boundaries through the source duration: {boundary_lines}.",
        "",
        "## Artifact-to-boundary proximity",
        "",
        *segment_lines,
        "",
        "## Local A/B/C outputs",
        "",
        "Each folder contains the unmodified source slice, the matching slice of the whole-song conversion, 30/40/50-step local conversions (all cropped back to the exact source-window duration), and `comparison.wav` in this order: current, 1s silence, steps30, 1s silence, steps40, 1s silence, steps50. No normalization, limiter, compressor, pitch shift, or time stretch is applied.",
        "",
        "## Optional F0 diagnostic",
        "",
        *f0_lines,
        "",
        "These events are diagnostic flags only; they do not determine perceived quality or select a preferred output. Review `f0_diagnostic.json` alongside listening.",
        "",
        "## Future short-reference context (not run)",
        "",
        *future_context,
        "",
        "## Interpretation",
        "",
        "No automated quality verdict is made. Compare every `comparison.wav` by ear. If the whole-song slice has the artifact and all local 30/40/50 variants do not, that supports a long-audio chunk/context cause; if variants differ systematically by step count, it supports diffusion sensitivity; if all variants fail on the same syllable, inspect F0/semantic/source ambiguity. Boundary proximity alone is not a conclusion.",
        "",
    ])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job-root", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True, help="local-only pipeline.json")
    parser.add_argument("--ranges", type=parse_ranges, required=True)
    parser.add_argument("--steps", type=parse_steps, default=[30, 40, 50])
    parser.add_argument("--labels", help="optional comma-separated folder labels; one per range")
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--resume", action="store_true", help="reuse complete step WAVs in an interrupted diagnostic output")
    args = parser.parse_args()

    job_root = args.job_root.resolve()
    cfg = json.loads(args.config.read_text(encoding="utf-8"))
    seed = cfg["seed_vc"]
    source_full = job_root / "stems" / "vocals_for_seedvc.wav"
    current_full = job_root / "vocal" / "yogurt_vocal.wav"
    target = Path(seed["target_reference"])
    runtime_path = Path(seed["runtime_config"])
    wrapper = Path(seed["wrapper"])
    for path in (source_full, current_full, target, runtime_path, wrapper):
        if not path.is_file():
            raise FileNotFoundError(path)
    labels = args.labels.split(",") if args.labels else [f"{int(start):03d}s" for start, _ in args.ranges]
    if len(labels) != len(args.ranges) or len(set(labels)) != len(labels):
        raise ValueError("--labels must provide unique labels matching --ranges")
    timestamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    output_root = (args.output_root or job_root / "diagnostics" / f"seedvc_artifact_{timestamp}").resolve()
    output_root.mkdir(parents=True, exist_ok=args.resume)
    runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
    reference = audio_info(target)
    source_info = audio_info(source_full)
    current_info = audio_info(current_full)
    boundaries = calculate_boundaries(
        source_duration=source_info["duration_seconds"], sample_rate=reference["sample_rate"],
        reference_duration=reference["duration_seconds"], n_fft=2048, hop_length=512,
    )
    (output_root / "chunk_boundaries.json").write_text(json.dumps(boundaries, indent=2), encoding="utf-8")
    manifest: dict[str, Any] = {
        "tool": "diagnose_seedvc_artifacts.py",
        "job_root_basename": job_root.name,
        "source": source_info,
        "current_fullsong_conversion": current_info,
        "target_reference": reference,
        "seed_vc": {"pinned_commit": runtime.get("seed_vc_commit"), "production_diffusion_steps": seed.get("diffusion_steps"), "tail_pad_seconds": seed.get("tail_pad_seconds", 5.0), "f0_condition": True, "auto_f0_adjust": False, "semitone_shift": 0, "length_adjust": 1.0, "inference_cfg_rate": 0.7},
        "segments": [],
    }
    f0_results: dict[str, Any] = {}
    for (start, end), label in zip(args.ranges, labels, strict=True):
        segment = output_root / label
        segment.mkdir(exist_ok=args.resume)
        source_cut = segment / "source.wav"
        current_cut = segment / "current_fullsong_conversion.wav"
        cut = (
            {"start_seconds": start, "end_seconds": end, "frames": sf.info(source_cut).frames, "sample_rate": sf.info(source_cut).samplerate}
            if args.resume and source_cut.is_file()
            else cut_audio(source_full, source_cut, start, end)
        )
        if not (args.resume and current_cut.is_file()):
            cut_audio(current_full, current_cut, start, end)
        padded_source = segment / "source_with_tail_pad.wav"
        write_padded_copy(source_cut, padded_source, float(seed.get("tail_pad_seconds", 5.0)))
        generated: list[Path] = []
        for step in args.steps:
            work_output = segment / f"seedvc_output_steps{step}"
            final = segment / f"steps{step}.wav"
            if not (args.resume and final.is_file()):
                actual = run_wrapper(wrapper, runtime, padded_source, target, work_output, step)
                trim_to_duration(actual, final, end - start)
            generated.append(final)
            if work_output.exists():
                shutil.rmtree(work_output)
        padded_source.unlink()
        comparison = segment / "comparison.wav"
        concat_comparison([current_cut, *generated], comparison)
        files = [source_cut, current_cut, *generated]
        f0_results[label] = {path.name: f0_diagnostic(path) for path in files}
        manifest["segments"].append({
            "label": label,
            "range": cut,
            "boundary_proximity": nearest_boundary((start + end) / 2, boundaries["boundaries_seconds"]),
            "metrics": {path.name: basic_metrics(path) for path in [*files, comparison]},
        })
    (output_root / "f0_diagnostic.json").write_text(json.dumps(f0_results, indent=2), encoding="utf-8")
    (output_root / "diagnostic_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    (output_root / "DIAGNOSTIC_REPORT.md").write_text(render_report(manifest, boundaries, f0_results), encoding="utf-8")
    print(output_root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
