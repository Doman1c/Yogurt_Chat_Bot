from __future__ import annotations

import asyncio
import tempfile
from dataclasses import dataclass
from pathlib import Path

from ..assets import VocalArtifact
from ..schemas import SingingProfile
from .base import ConversionOptions
from .runtime import (
    TrustedRuntimeConfig,
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
    speaker_id: int = 0
    index_rate: float = 0.75

    def __post_init__(self) -> None:
        if self.speaker_id < 0 or not 0.0 <= self.index_rate <= 1.0:
            raise ValueError("invalid_rvc_backend_config")


class RVCAdapter:
    """Fixed-argv adapter for a locally trained, trusted Yogurt RVC profile."""

    name = "rvc"

    def __init__(self, config: RVCBackendConfig, *, revision: str) -> None:
        self._config = config
        self.version = revision

    async def convert(
        self,
        vocal: VocalArtifact,
        profile: SingingProfile,
        options: ConversionOptions,
        cancelled: asyncio.Event,
    ) -> VocalArtifact:
        del profile
        config = self._config
        require_absolute_file(config.cli_script, label="cli_script")
        require_absolute_file(config.model, label="model")
        if config.index is not None:
            require_absolute_file(config.index, label="index")
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
            await run_trusted_process(command, config.runtime, cancelled)
            return read_validated_wav(output, config.runtime, expected_sample_rate=44_100)
