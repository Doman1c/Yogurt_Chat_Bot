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
class Vevo2BackendConfig:
    """Application-owned bridge to the pinned Vevo2 FM inference API."""

    runtime: TrustedRuntimeConfig
    runner_script: Path
    amphion_root: Path
    model_root: Path
    reference_audio: Path
    dependencies: tuple[RuntimeModelDependency, ...] = ()


class Vevo2Adapter:
    """Fixed wrapper adapter; it never invokes Amphion's example WebUI paths."""

    name = "vevo2"

    def __init__(self, config: Vevo2BackendConfig, *, revision: str) -> None:
        self._config = config
        self.version = revision

    async def preflight(self) -> str:
        config = self._config
        try:
            require_absolute_file(config.runner_script, label="runner_script")
            require_absolute_file(config.reference_audio, label="reference_audio")
            if (
                not config.amphion_root.is_absolute()
                or not config.amphion_root.is_dir()
                or not config.model_root.is_absolute()
                or not config.model_root.is_dir()
            ):
                raise SingingError("engine_unavailable")
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
        if options.transpose_semitones != 0:
            # The pinned FM API has no documented semitone argument.
            raise SingingError("unsupported_transpose")
        config = self._config
        await self.preflight()
        if (
            not config.amphion_root.is_absolute()
            or not config.amphion_root.is_dir()
            or not config.model_root.is_absolute()
            or not config.model_root.is_dir()
        ):
            raise SingingError("engine_unavailable")
        with tempfile.TemporaryDirectory(prefix="yogurt-vevo2-") as temp_dir:
            directory = Path(temp_dir)
            source = write_private_input(directory, vocal)
            output = directory / "converted.wav"
            try:
                await run_trusted_process(
                    (
                    config.runtime.python_executable,
                    config.runner_script,
                    "--source",
                    source,
                    "--reference",
                    config.reference_audio,
                    "--output",
                    output,
                    "--model-root",
                    config.model_root,
                    "--amphion-root",
                    config.amphion_root,
                ),
                    config.runtime,
                    cancelled,
                    environment=offline_environment(),
                )
            except TrustedProcessFailure as error:
                code = "engine_unavailable" if error.kind == "spawn" else "conversion_failed"
                raise SingingError(code) from None
            return read_validated_wav(output, config.runtime, expected_sample_rate=None)
