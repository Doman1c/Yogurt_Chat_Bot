from __future__ import annotations

import asyncio
import tempfile
from dataclasses import dataclass
from pathlib import Path

from ..assets import InstrumentalArtifact, SeparatedSong, SongAsset
from ..errors import SingingError
from ..engines.runtime import (
    RuntimePreflightFailure,
    TrustedRuntimeConfig,
    TrustedProcessFailure,
    offline_environment,
    preflight_runtime,
    read_validated_wav,
    run_trusted_process,
)


_DEMUCS_REPO_PREFLIGHT_RUNNER = Path(__file__).with_name("demucs_repo_preflight.py").resolve()


@dataclass(frozen=True)
class DemucsBackendConfig:
    """Application-owned local Demucs command composition."""

    runtime: TrustedRuntimeConfig
    revision: str
    model_repo: Path
    model_name: str = "htdemucs"

    def __post_init__(self) -> None:
        if self.model_name not in {"htdemucs", "htdemucs_ft"}:
            raise ValueError("invalid_demucs_model")
        if not self.model_repo.is_absolute():
            raise ValueError("demucs_model_repo_must_be_absolute")

    def validate_model_repo(self) -> None:
        if not self.model_repo.is_dir():
            raise RuntimePreflightFailure("demucs_repo_missing")


class DemucsSeparator:
    """Optional fixed-argv Demucs adapter; no request controls its runtime."""

    name = "demucs"

    def __init__(self, config: DemucsBackendConfig | None = None) -> None:
        self._config = config

    async def preflight(self) -> str:
        config = self._config
        if config is None:
            raise SingingError("separator_unavailable")
        try:
            config.validate_model_repo()
            revision = await preflight_runtime(config.runtime, config.revision, ())
            await preflight_demucs_repo(config)
            return revision
        except (RuntimePreflightFailure, SingingError):
            raise SingingError("separator_unavailable") from None

    async def separate(self, asset: SongAsset, cancelled: asyncio.Event) -> SeparatedSong:
        config = self._config
        if config is None:
            raise SingingError("separator_unavailable")
        await self.preflight()
        if not asset.local_path.is_file():
            raise SingingError("unknown_asset")
        with tempfile.TemporaryDirectory(prefix="yogurt-demucs-") as temp_dir:
            output_root = Path(temp_dir)
            try:
                await run_trusted_process(
                    (
                    config.runtime.python_executable,
                    "-m",
                    "demucs.separate",
                    "-n",
                    config.model_name,
                    "--repo",
                    config.model_repo,
                    "--two-stems",
                    "vocals",
                    "-o",
                    output_root,
                    asset.local_path,
                ),
                    config.runtime,
                    cancelled,
                    environment=offline_environment(),
                )
            except TrustedProcessFailure as error:
                code = "separator_unavailable" if error.kind == "spawn" else "separation_failed"
                raise SingingError(code) from None
            stem_root = output_root / config.model_name / asset.local_path.stem
            try:
                vocal = read_validated_wav(
                    stem_root / "vocals.wav", config.runtime, expected_sample_rate=None
                )
                instrumental = read_validated_wav(
                    stem_root / "no_vocals.wav", config.runtime, expected_sample_rate=None
                )
            except SingingError as error:
                raise SingingError("separation_failed") from error
            return SeparatedSong(
                vocal=vocal,
                instrumental=InstrumentalArtifact(
                    audio=instrumental.audio,
                    duration_ms=instrumental.duration_ms,
                    sample_rate=instrumental.sample_rate,
                ),
            )


async def preflight_demucs_repo(config: DemucsBackendConfig) -> None:
    """Use Demucs' own LocalRepo semantics to validate the selected bag."""

    if not _DEMUCS_REPO_PREFLIGHT_RUNNER.is_file():
        raise RuntimePreflightFailure("demucs_repo_preflight_runner_missing")
    try:
        await run_trusted_process(
            (
                config.runtime.python_executable,
                _DEMUCS_REPO_PREFLIGHT_RUNNER,
                "--repo",
                config.model_repo,
                "--model-name",
                config.model_name,
            ),
            config.runtime,
            asyncio.Event(),
            environment=offline_environment(),
        )
    except (TrustedProcessFailure, SingingError) as error:
        raise RuntimePreflightFailure("demucs_model_material_invalid") from error
