"""Run one preconfigured local SVC bake-off job without exposing runtime controls.

The only accepted inputs are opaque sample, engine, language, and bounded
transpose selectors.  The ignored ``singing_runtime/config.toml`` owns every
private path and model selection.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import time
import uuid
from pathlib import Path
from typing import Any

from .assets import VocalArtifact
from .engines import RVCAdapter, SeedVCAdapter, Vevo2Adapter
from .engines.base import ConversionOptions, SingingConversionEngine
from .engines.rvc import RVCBackendConfig
from .engines.runtime import RuntimeModelDependency, TrustedRuntimeConfig, read_validated_wav
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
    parser.add_argument("--preflight-only", action="store_true")
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
    if not isinstance(value, str):
        raise RuntimeError("invalid_private_runtime_config")
    path = Path(str(value))
    if not path.is_absolute():
        raise RuntimeError("private runtime paths must be absolute")
    return path


def _runtime(values: dict[str, Any]) -> TrustedRuntimeConfig:
    try:
        return TrustedRuntimeConfig(
            python_executable=_path(values["python_executable"]),
            working_directory=_path(values["working_directory"]),
            timeout_seconds=float(values.get("timeout_seconds", 180)),
            max_output_bytes=int(values.get("max_output_bytes", 16 * 1024 * 1024)),
            max_output_duration_ms=int(values.get("max_output_duration_ms", 70_000)),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise RuntimeError("invalid_private_runtime_config") from error


def _dependencies(values: dict[str, Any]) -> tuple[RuntimeModelDependency, ...]:
    raw_dependencies = values.get("dependencies", {})
    if not isinstance(raw_dependencies, dict):
        raise RuntimeError("invalid_private_runtime_config")
    try:
        dependencies = []
        for component_id, details in sorted(raw_dependencies.items()):
            if not isinstance(details, dict):
                raise ValueError("invalid_private_runtime_config")
            required = details.get("required", True)
            source_revision = details.get("source_revision")
            expected_size_bytes = details.get("expected_size_bytes")
            if (
                not isinstance(required, bool)
                or source_revision is not None
                and not isinstance(source_revision, str)
                or expected_size_bytes is not None
                and (not isinstance(expected_size_bytes, int) or isinstance(expected_size_bytes, bool))
            ):
                raise ValueError("invalid_private_runtime_config")
            dependencies.append(
                RuntimeModelDependency(
                    component_id=component_id,
                    local_path=_path(details["local_path"]),
                    required=required,
                    source_revision=source_revision,
                    expected_size_bytes=expected_size_bytes,
                )
            )
        return tuple(dependencies)
    except (KeyError, TypeError, ValueError) as error:
        raise RuntimeError("invalid_private_runtime_config") from error


def _provenance_id(value: Any) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[a-z][a-z0-9._-]{0,63}", value) is None:
        raise RuntimeError("invalid_private_runtime_config")
    return value


def _engine(
    data: dict[str, Any], name: str, runtime: TrustedRuntimeConfig
) -> tuple[SingingConversionEngine, str]:
    try:
        values = data["engines"][name]
        profile = data["profile"]
        if not isinstance(values, dict) or not isinstance(profile, dict):
            raise ValueError("invalid_private_runtime_config")
        revision = values["revision"]
        if not isinstance(revision, str):
            raise ValueError("invalid_private_runtime_config")
        dependencies = _dependencies(values)
        if name == "seed_vc":
            model_id = _provenance_id(values["model_id"])
            return SeedVCAdapter(
                SeedVCBackendConfig(
                    runtime=runtime,
                    inference_script=_path(values["inference_script"]),
                    checkpoint=_path(values["checkpoint"]),
                    model_config=_path(values["model_config"]),
                    reference_audio=_path(profile["reference_audio"]),
                    dependencies=dependencies,
                    model_id=model_id,
                    diffusion_steps=int(values.get("diffusion_steps", 30)),
                    inference_cfg_rate=float(values.get("inference_cfg_rate", 0.7)),
                ),
                revision=revision,
            ), model_id
        if name == "rvc":
            index = values.get("index")
            model_id = _provenance_id(values["model_id"])
            return RVCAdapter(
                RVCBackendConfig(
                    runtime=runtime,
                    cli_script=_path(values["cli_script"]),
                    model=_path(values["model"]),
                    index=_path(index) if index else None,
                    dependencies=dependencies,
                    model_id=model_id,
                    speaker_id=int(values.get("speaker_id", 0)),
                    index_rate=float(values.get("index_rate", 0.75)),
                ),
                revision=revision,
            ), model_id
        model_id = _provenance_id(values["model_id"])
        return Vevo2Adapter(
            Vevo2BackendConfig(
                runtime=runtime,
                runner_script=_path(values["runner_script"]),
                amphion_root=_path(values["amphion_root"]),
                model_root=_path(values["model_root"]),
                reference_audio=_path(profile["reference_audio"]),
                dependencies=dependencies,
                model_id=model_id,
            ),
            revision=revision,
        ), model_id
    except (KeyError, TypeError, ValueError) as error:
        raise RuntimeError("invalid_private_runtime_config") from error


async def _preflight(
    args: argparse.Namespace, config_path: Path
) -> tuple[SingingConversionEngine, VocalArtifact, TrustedRuntimeConfig, Path, str, str, str]:
    if re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", args.sample_id) is None:
        raise SingingError("invalid_request")
    try:
        data = _load_toml(config_path)
        engines = data["engines"]
        samples = data["samples"]
        outputs = data["outputs"]
        if not isinstance(engines, dict) or not isinstance(samples, dict) or not isinstance(outputs, dict):
            raise ValueError("invalid_private_runtime_config")
        engine_values = engines[args.engine]
        sample = samples[args.sample_id]
        if not isinstance(engine_values, dict) or not isinstance(sample, dict):
            raise ValueError("invalid_private_runtime_config")
        if sample["language"] != args.language:
            raise SingingError("invalid_request")
        profile_id = _provenance_id(data["profile"]["profile_id"])
        runtime = _runtime(engine_values)
        source = read_validated_wav(_path(sample["vocal_audio"]), runtime, expected_sample_rate=None)
        output_root = _path(outputs["directory"])
        output_root.mkdir(parents=True, exist_ok=True)
        engine, model_id = _engine(data, args.engine, runtime)
        verified_revision = await engine.preflight()  # type: ignore[attr-defined]
        return engine, source, runtime, output_root, verified_revision, model_id, profile_id
    except SingingError:
        raise
    except (KeyError, OSError, RuntimeError, TypeError, ValueError):
        raise SingingError("engine_unavailable") from None


async def run(args: argparse.Namespace, config_path: Path = Path("singing_runtime/config.toml")) -> BakeoffRunManifest:
    engine, source, runtime, output_root, verified_revision, model_id, profile_id = await _preflight(
        args, config_path
    )
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
        engine_revision=verified_revision,
        model_id=model_id,
        profile_id=profile_id,
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
    (output_root / f"{manifest.run_id}.json").write_text(manifest.model_dump_json(indent=2))
    if output is not None:
        (output_root / f"{manifest.run_id}.wav").write_bytes(output.audio)
    return manifest


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.preflight_only:
            _, _, _, _, verified_revision, model_id, profile_id = asyncio.run(
                _preflight(args, Path("singing_runtime/config.toml"))
            )
            print(
                json.dumps(
                    {
                        "success": True,
                        "status": "READY",
                        "engine_revision": verified_revision,
                        "model_id": model_id,
                        "profile_id": profile_id,
                    }
                )
            )
            return 0
        manifest = asyncio.run(run(args))
    except SingingError as error:
        print(json.dumps({"success": False, "error_code": error.code}))
        return 2
    except (RuntimeError, ValueError):
        print(json.dumps({"success": False, "error_code": "engine_unavailable"}))
        return 2
    print(manifest.model_dump_json())
    return 0 if manifest.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
