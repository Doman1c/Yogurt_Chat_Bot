from __future__ import annotations

import asyncio
import tempfile
from dataclasses import dataclass
from pathlib import Path

from ..assets import VocalArtifact
from ..errors import SingingError
from ..schemas import SingingProfile
from .base import ConversionOptions
from .runtime import (
    RuntimeModelDependency,
    RuntimePreflightFailure,
    TrustedRuntimeConfig,
    TrustedProcessFailure,
    offline_environment,
    preflight_runtime,
    read_validated_wav,
    require_absolute_file,
    run_trusted_process,
    write_private_input,
)


@dataclass(frozen=True)
class RVCBackendConfig:
    """Application-owned pin for RVC's official offline ``infer/cli.py``."""

    runtime: TrustedRuntimeConfig
    cli_script: Path
    model: Path
    index: Path | None = None
    dependencies: tuple[RuntimeModelDependency, ...] = ()
    speaker_id: int = 0
    index_rate: float = 0.75

    def __post_init__(self) -> None:
        if self.speaker_id < 0 or not 0.0 <= self.index_rate <= 1.0:
            raise ValueError("invalid_rvc_backend_config")
        if {dependency.component_id for dependency in self.dependencies} != {"rvc_runtime_assets"}:
            raise ValueError("incomplete_rvc_dependency_inventory")


class RVCAdapter:
    """Fixed-argv adapter for a locally trained, trusted Yogurt RVC profile."""

    name = "rvc"

    def __init__(self, config: RVCBackendConfig, *, revision: str) -> None:
        self._config = config
        self.version = revision

    async def preflight(self) -> str:
        config = self._config
        try:
            require_absolute_file(config.cli_script, label="cli_script")
            require_absolute_file(config.model, label="model")
            if config.index is not None:
                require_absolute_file(config.index, label="index")
            return await preflight_runtime(config.runtime, self.version, config.dependencies)
        except (RuntimePreflightFailure, SingingError):
            raise SingingError("engine_unavailable") from None

    async def convert(
        self,
        vocal: VocalArtifact,
        profile: SingingProfile,
        options: ConversionOptions,
        cancelled: asyncio.Event,
    ) -> VocalArtifact:
        del profile
        config = self._config
        await self.preflight()
        with tempfile.TemporaryDirectory(prefix="yogurt-rvc-") as temp_dir:
            directory = Path(temp_dir)
            source = write_private_input(directory, vocal)
            output = directory / "converted.wav"
            command: list[str | Path] = [
                config.runtime.python_executable,
                config.cli_script,
                "--model",
                config.model,
                "--input",
                source,
                "--output",
                output,
                "--speaker-id",
                str(config.speaker_id),
                "--pitch",
                str(options.transpose_semitones),
                "--f0-method",
                "rmvpe",
                "--index-rate",
                str(config.index_rate if config.index is not None else 0.0),
                "--resample-sr",
                "44100",
                "--format",
                "wav",
                "--overwrite",
            ]
            if config.index is not None:
                command.extend(("--index", config.index))
            try:
                await run_trusted_process(
                    command, config.runtime, cancelled, environment=offline_environment()
                )
            except TrustedProcessFailure as error:
                code = "engine_unavailable" if error.kind == "spawn" else "conversion_failed"
                raise SingingError(code) from None
            return read_validated_wav(output, config.runtime, expected_sample_rate=44_100)
