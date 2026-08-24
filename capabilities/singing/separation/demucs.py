from __future__ import annotations

import asyncio
import tempfile
from dataclasses import dataclass
from pathlib import Path

from ..assets import InstrumentalArtifact, SeparatedSong, SongAsset
from ..errors import SingingError
from ..engines.runtime import (
    TrustedRuntimeConfig,
    read_validated_wav,
    require_absolute_file,
    run_trusted_process,
)


@dataclass(frozen=True)
class DemucsBackendConfig:
    """Application-owned local Demucs command composition."""

    runtime: TrustedRuntimeConfig
    model_name: str = "htdemucs"

    def __post_init__(self) -> None:
        if self.model_name not in {"htdemucs", "htdemucs_ft"}:
            raise ValueError("invalid_demucs_model")


class DemucsSeparator:
    """Optional fixed-argv Demucs adapter; no request controls its runtime."""

    name = "demucs"

    def __init__(self, config: DemucsBackendConfig | None = None) -> None:
        self._config = config

    async def separate(self, asset: SongAsset, cancelled: asyncio.Event) -> SeparatedSong:
        config = self._config
        if config is None:
            raise SingingError("separator_unavailable")
        require_absolute_file(config.runtime.python_executable, label="python_executable")
        if not asset.local_path.is_file():
            raise SingingError("unknown_asset")
        with tempfile.TemporaryDirectory(prefix="yogurt-demucs-") as temp_dir:
            output_root = Path(temp_dir)
            await run_trusted_process(
                (
                    config.runtime.python_executable,
                    "-m",
                    "demucs.separate",
                    "-n",
                    config.model_name,
                    "--two-stems",
                    "vocals",
                    "-o",
                    output_root,
                    asset.local_path,
                ),
                config.runtime,
                cancelled,
            )
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
