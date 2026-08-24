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
class Vevo2BackendConfig:
    """Application-owned bridge to the pinned Vevo2 FM inference API."""

    runtime: TrustedRuntimeConfig
    runner_script: Path
    amphion_root: Path
    model_root: Path
    reference_audio: Path


class Vevo2Adapter:
    """Fixed wrapper adapter; it never invokes Amphion's example WebUI paths."""

    name = "vevo2"

    def __init__(self, config: Vevo2BackendConfig, *, revision: str) -> None:
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
        if options.transpose_semitones != 0:
            # The pinned FM API has no documented semitone argument.
            from ..errors import SingingError

            raise SingingError("unsupported_transpose")
        config = self._config
        require_absolute_file(config.runner_script, label="runner_script")
        require_absolute_file(config.reference_audio, label="reference_audio")
        if (
            not config.amphion_root.is_absolute()
            or not config.amphion_root.is_dir()
            or not config.model_root.is_absolute()
            or not config.model_root.is_dir()
        ):
            from ..errors import SingingError

            raise SingingError("engine_unavailable")
        with tempfile.TemporaryDirectory(prefix="yogurt-vevo2-") as temp_dir:
            directory = Path(temp_dir)
            source = write_private_input(directory, vocal)
            output = directory / "converted.wav"
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
            )
            return read_validated_wav(output, config.runtime, expected_sample_rate=None)
