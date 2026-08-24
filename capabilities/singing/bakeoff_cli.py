"""Run one preconfigured local SVC bake-off job without exposing runtime controls.

The only accepted inputs are opaque sample, engine, language, and bounded
transpose selectors.  The ignored ``singing_runtime/config.toml`` owns every
private path and model selection.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
import uuid
from pathlib import Path
from typing import Any

from .assets import VocalArtifact
from .engines import RVCAdapter, SeedVCAdapter, Vevo2Adapter
from .engines.base import ConversionOptions, SingingConversionEngine
from .engines.rvc import RVCBackendConfig
from .engines.runtime import TrustedRuntimeConfig, read_validated_wav
from .engines.seed_vc import SeedVCBackendConfig
from .engines.vevo2 import Vevo2BackendConfig
from .errors import SingingError
from .schemas import BakeoffRunManifest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run one trusted local Yogurt SVC bake-off job")
    parser.add_argument("--sample-id", required=True, choices=None)
    parser.add_argument("--engine", required=True, choices=("seed_vc", "rvc", "vevo2"))
    parser.add_argument("--language", required=True, choices=("zh", "ja", "en"))
    parser.add_argument("--transpose", type=int, default=0, choices=range(-12, 13))
    return parser


def _load_toml(path: Path) -> dict[str, Any]:
    try:
        import tomllib  # Python 3.11+
    except ModuleNotFoundError:  # Optional only in the isolated engine runtime.
        try:
            import tomli as tomllib  # type: ignore[no-redef]
        except ModuleNotFoundError as error:
            raise RuntimeError("install tomli in the isolated runtime") from error
    try:
        with path.open("rb") as stream:
            return tomllib.load(stream)
    except (OSError, ValueError) as error:
        raise RuntimeError("private singing runtime config is unavailable") from error


def _path(value: Any) -> Path:
    path = Path(str(value))
    if not path.is_absolute():
        raise RuntimeError("private runtime paths must be absolute")
    return path


def _runtime(values: dict[str, Any]) -> TrustedRuntimeConfig:
    return TrustedRuntimeConfig(
        python_executable=_path(values["python_executable"]),
        working_directory=_path(values["working_directory"]),
        timeout_seconds=float(values.get("timeout_seconds", 180)),
        max_output_bytes=int(values.get("max_output_bytes", 16 * 1024 * 1024)),
        max_output_duration_ms=int(values.get("max_output_duration_ms", 70_000)),
    )


def _engine(data: dict[str, Any], name: str, runtime: TrustedRuntimeConfig) -> SingingConversionEngine:
    values = data["engines"][name]
    revision = str(values["revision"])
    if name == "seed_vc":
        return SeedVCAdapter(
            SeedVCBackendConfig(
                runtime=runtime,
                inference_script=_path(values["inference_script"]),
                checkpoint=_path(values["checkpoint"]),
                model_config=_path(values["model_config"]),
                reference_audio=_path(data["profile"]["reference_audio"]),
                diffusion_steps=int(values.get("diffusion_steps", 30)),
                inference_cfg_rate=float(values.get("inference_cfg_rate", 0.7)),
            ),
            revision=revision,
        )
    if name == "rvc":
        index = values.get("index")
        return RVCAdapter(
            RVCBackendConfig(
                runtime=runtime,
                cli_script=_path(values["cli_script"]),
                model=_path(values["model"]),
                index=_path(index) if index else None,
                speaker_id=int(values.get("speaker_id", 0)),
                index_rate=float(values.get("index_rate", 0.75)),
            ),
            revision=revision,
        )
    return Vevo2Adapter(
        Vevo2BackendConfig(
            runtime=runtime,
            runner_script=_path(values["runner_script"]),
            amphion_root=_path(values["amphion_root"]),
            model_root=_path(values["model_root"]),
            reference_audio=_path(data["profile"]["reference_audio"]),
        ),
        revision=revision,
    )


async def run(args: argparse.Namespace, config_path: Path = Path("singing_runtime/config.toml")) -> BakeoffRunManifest:
    data = _load_toml(config_path)
    engine_values = data["engines"][args.engine]
    runtime = _runtime(engine_values)
    sample = data["samples"][args.sample_id]
    if sample["language"] != args.language:
        raise RuntimeError("sample language does not match the requested language")
    source = read_validated_wav(_path(sample["vocal_audio"]), runtime, expected_sample_rate=None)
    engine = _engine(data, args.engine, runtime)
    started_at = time.monotonic()
    try:
        output = await engine.convert(
            source,
            "yogurt",
            ConversionOptions(args.transpose, True, True, "medium"),
            asyncio.Event(),
        )
    except SingingError as error:
        output = None
        error_code = error.code
    else:
        error_code = None
    wall_time_ms = round((time.monotonic() - started_at) * 1000)
    manifest = BakeoffRunManifest(
        run_id=str(uuid.uuid4()),
        engine=engine.name,
        engine_revision=engine.version,
        sample_id=args.sample_id,
        language=args.language,
        source_duration_ms=source.duration_ms,
        wall_time_ms=wall_time_ms,
        output_duration_ms=output.duration_ms if output else None,
        sample_rate=output.sample_rate if output else None,
        transpose_semitones=args.transpose,
        duration_ratio=(output.duration_ms / source.duration_ms) if output else None,
        success=output is not None,
        error_code=error_code,
    )
    output_root = _path(data["outputs"]["directory"])
    output_root.mkdir(parents=True, exist_ok=True)
    (output_root / f"{manifest.run_id}.json").write_text(manifest.model_dump_json(indent=2))
    if output is not None:
        (output_root / f"{manifest.run_id}.wav").write_bytes(output.audio)
    return manifest


def main(argv: list[str] | None = None) -> int:
    manifest = asyncio.run(run(build_parser().parse_args(argv)))
    print(manifest.model_dump_json())
    return 0 if manifest.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
