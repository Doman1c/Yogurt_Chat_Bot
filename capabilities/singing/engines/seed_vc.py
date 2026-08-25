from __future__ import annotations

import asyncio
import re
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
    find_only_wav,
    offline_environment,
    preflight_runtime,
    read_validated_wav,
    require_absolute_file,
    run_trusted_process,
    write_private_input,
)


_SEEDVC_CACHE_PREFLIGHT_RUNNER = Path(__file__).with_name("seed_vc_cache_preflight.py").resolve()


@dataclass(frozen=True)
class SeedVCCacheLayout:
    """The two cache roots hard-coded by the pinned Seed-VC inference path."""

    checkpoints_root: Path
    hf_cache_root: Path

    @classmethod
    def for_runtime(cls, runtime: TrustedRuntimeConfig) -> "SeedVCCacheLayout":
        checkpoints_root = runtime.working_directory / "checkpoints"
        return cls(checkpoints_root, checkpoints_root / "hf_cache")

    def validate(self, dependencies: tuple[RuntimeModelDependency, ...]) -> None:
        roots = {
            "rmvpe": self.checkpoints_root,
            "campplus": self.checkpoints_root,
            "bigvgan": self.hf_cache_root,
            "whisper": self.hf_cache_root,
        }
        for dependency in dependencies:
            if dependency.local_path != roots[dependency.component_id]:
                raise RuntimePreflightFailure("seedvc_cache_layout_invalid")


@dataclass(frozen=True)
class SeedVCBackendConfig:
    """Application-owned pin for the official Seed-VC V1 SVC command."""

    runtime: TrustedRuntimeConfig
    inference_script: Path
    checkpoint: Path
    model_config: Path
    reference_audio: Path
    dependencies: tuple[RuntimeModelDependency, ...]
    model_id: str = "local-model"
    diffusion_steps: int = 30
    inference_cfg_rate: float = 0.7
    cache_layout: SeedVCCacheLayout | None = None

    def __post_init__(self) -> None:
        if not 30 <= self.diffusion_steps <= 50:
            raise ValueError("invalid_seedvc_diffusion_steps")
        if not 0.0 <= self.inference_cfg_rate <= 1.0:
            raise ValueError("invalid_seedvc_cfg_rate")
        required_components = {"rmvpe", "campplus", "bigvgan", "whisper"}
        if {dependency.component_id for dependency in self.dependencies} != required_components:
            raise ValueError("incomplete_seedvc_dependency_inventory")
        if not _is_provenance_id(self.model_id):
            raise ValueError("invalid_seedvc_model_id")
        expected_layout = SeedVCCacheLayout.for_runtime(self.runtime)
        if self.cache_layout is not None and self.cache_layout != expected_layout:
            raise ValueError("seedvc_cache_layout_must_match_runtime")
        object.__setattr__(self, "cache_layout", expected_layout)


class SeedVCAdapter:
    """Fixed-argv adapter for the pinned official Seed-VC 44.1 kHz SVC path."""

    name = "seed_vc"

    def __init__(self, config: SeedVCBackendConfig, *, revision: str) -> None:
        self._config = config
        self.version = revision
        self.model_id = config.model_id

    async def preflight(self) -> str:
        config = self._config
        try:
            assert config.cache_layout is not None
            config.cache_layout.validate(config.dependencies)
            for path, label in (
                (config.inference_script, "inference_script"),
                (config.checkpoint, "checkpoint"),
                (config.model_config, "model_config"),
                (config.reference_audio, "reference_audio"),
            ):
                require_absolute_file(path, label=label)
            revision = await preflight_runtime(config.runtime, self.version, config.dependencies)
            await preflight_seedvc_cache(config.runtime)
            return revision
        except (RuntimePreflightFailure, TrustedProcessFailure, SingingError):
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
        with tempfile.TemporaryDirectory(prefix="yogurt-seedvc-") as temp_dir:
            directory = Path(temp_dir)
            source = write_private_input(directory, vocal)
            output = directory / "output"
            output.mkdir()
            try:
                await run_trusted_process(
                    (
                    config.runtime.python_executable,
                    config.inference_script,
                    "--source",
                    source,
                    "--target",
                    config.reference_audio,
                    "--output",
                    output,
                    "--diffusion-steps",
                    str(config.diffusion_steps),
                    "--length-adjust",
                    "1.0",
                    "--inference-cfg-rate",
                    str(config.inference_cfg_rate),
                    "--f0-condition",
                    "True",
                    "--auto-f0-adjust",
                    "False",
                    "--semi-tone-shift",
                    str(options.transpose_semitones),
                    "--checkpoint",
                    config.checkpoint,
                    "--config",
                    config.model_config,
                    "--fp16",
                    "True",
                ),
                    config.runtime,
                    cancelled,
                    environment=offline_environment(),
                )
            except TrustedProcessFailure as error:
                code = "engine_unavailable" if error.kind == "spawn" else "conversion_failed"
                raise SingingError(code) from None
            return read_validated_wav(
                find_only_wav(output), config.runtime, expected_sample_rate=44_100
            )


def _is_provenance_id(value: str) -> bool:
    return re.fullmatch(r"[a-z][a-z0-9._-]{0,63}", value) is not None


async def preflight_seedvc_cache(runtime: TrustedRuntimeConfig) -> None:
    """Let the pinned runtime's Hugging Face stack resolve its offline cache."""

    require_absolute_file(_SEEDVC_CACHE_PREFLIGHT_RUNNER, label="seedvc_cache_preflight_runner")
    await run_trusted_process(
        (runtime.python_executable, _SEEDVC_CACHE_PREFLIGHT_RUNNER),
        runtime,
        asyncio.Event(),
        environment=offline_environment(),
    )
