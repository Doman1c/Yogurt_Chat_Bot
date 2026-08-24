from __future__ import annotations

import asyncio
import inspect
import io
import sys
import wave
from pathlib import Path

import pytest

from capabilities.singing.assets import VocalArtifact
from capabilities.singing.assets import SongAsset
from capabilities.singing.engines.base import ConversionOptions
from capabilities.singing.engines.rvc import RVCAdapter, RVCBackendConfig
from capabilities.singing.engines.runtime import TrustedRuntimeConfig
from capabilities.singing.engines.seed_vc import SeedVCAdapter, SeedVCBackendConfig
from capabilities.singing.errors import SingingError
from capabilities.singing.separation.demucs import DemucsBackendConfig, DemucsSeparator


def _wav_bytes(*, frames: int = 4410, sample_rate: int = 44100) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(sample_rate)
        writer.writeframes(b"\0\0" * frames)
    return output.getvalue()


def _write_backend(tmp_path: Path, body: str) -> Path:
    script = tmp_path / "backend.py"
    script.write_text(body)
    return script


def _runtime(tmp_path: Path, *, max_output_bytes: int = 16 * 1024 * 1024) -> TrustedRuntimeConfig:
    return TrustedRuntimeConfig(
        python_executable=Path(sys.executable).resolve(),
        working_directory=tmp_path,
        timeout_seconds=2,
        max_output_bytes=max_output_bytes,
    )


def _options() -> ConversionOptions:
    return ConversionOptions(0, True, True, "medium")


def _writing_backend(*, malformed: bool = False, oversized: bool = False) -> str:
    payload = "b'not-a-wav'" if malformed else "b'\\0\\0' * (30000 if %s else 4410)" % oversized
    return f"""
import pathlib
import sys
import wave
arguments = sys.argv
output = pathlib.Path(arguments[arguments.index('--output') + 1])
target = output if output.suffix else output / 'converted.wav'
target.parent.mkdir(parents=True, exist_ok=True)
if {malformed!r}:
    target.write_bytes({payload})
else:
    with wave.open(str(target), 'wb') as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(44100)
        writer.writeframes({payload})
"""


def test_seedvc_trusted_config_runs_fixed_backend_and_returns_valid_wav(tmp_path: Path) -> None:
    async def run() -> None:
        script = _write_backend(tmp_path, _writing_backend())
        checkpoint = tmp_path / "model.pth"
        model_config = tmp_path / "model.yml"
        reference = tmp_path / "reference.wav"
        checkpoint.write_bytes(b"model")
        model_config.write_text("model: seed")
        reference.write_bytes(_wav_bytes())
        adapter = SeedVCAdapter(
            SeedVCBackendConfig(_runtime(tmp_path), script, checkpoint, model_config, reference),
            revision="pin-1",
        )
        result = await adapter.convert(VocalArtifact(_wav_bytes(), 100, 44100), "yogurt", _options(), asyncio.Event())
        assert result.media_type == "audio/wav"
        assert result.sample_rate == 44100
        assert result.duration_ms == 100

    asyncio.run(run())


@pytest.mark.parametrize("malformed,oversized", [(True, False), (False, True)])
def test_real_adapter_rejects_malformed_or_oversized_wav(
    tmp_path: Path, malformed: bool, oversized: bool
) -> None:
    async def run() -> None:
        script = _write_backend(tmp_path, _writing_backend(malformed=malformed, oversized=oversized))
        model = tmp_path / "yogurt.pth"
        model.write_bytes(b"model")
        adapter = RVCAdapter(
            RVCBackendConfig(_runtime(tmp_path, max_output_bytes=44_100), script, model),
            revision="pin-1",
        )
        with pytest.raises(SingingError, match="conversion_failed"):
            await adapter.convert(VocalArtifact(_wav_bytes(), 100, 44100), "yogurt", _options(), asyncio.Event())

    asyncio.run(run())


def test_backend_exit_and_cancellation_are_bounded_and_redacted(tmp_path: Path) -> None:
    async def run() -> None:
        failing = _write_backend(tmp_path, "import sys; print('/private/model', file=sys.stderr); raise SystemExit(2)")
        model = tmp_path / "yogurt.pth"
        model.write_bytes(b"model")
        adapter = RVCAdapter(RVCBackendConfig(_runtime(tmp_path), failing, model), revision="pin-1")
        with pytest.raises(SingingError, match="conversion_failed") as failure:
            await adapter.convert(VocalArtifact(_wav_bytes(), 100, 44100), "yogurt", _options(), asyncio.Event())
        assert "/private" not in str(failure.value)

        sleeping = _write_backend(tmp_path, "import time; time.sleep(30)")
        adapter = RVCAdapter(RVCBackendConfig(_runtime(tmp_path), sleeping, model), revision="pin-1")
        cancelled = asyncio.Event()
        task = asyncio.create_task(
            adapter.convert(VocalArtifact(_wav_bytes(), 100, 44100), "yogurt", _options(), cancelled)
        )
        await asyncio.sleep(0.05)
        cancelled.set()
        with pytest.raises(SingingError, match="cancelled"):
            await task

    asyncio.run(run())


def test_real_subprocess_layer_is_fixed_argv_without_shell() -> None:
    source = inspect.getsource(__import__("capabilities.singing.engines.runtime", fromlist=["*"]))
    assert "create_subprocess_exec" in source
    assert "shell=True" not in source


def test_demucs_adapter_uses_trusted_fixed_backend_and_validates_stems(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_run(command, runtime, cancelled) -> None:
        output_root = Path(command[command.index("-o") + 1])
        source = Path(command[-1])
        stem_root = output_root / "htdemucs" / source.stem
        stem_root.mkdir(parents=True)
        (stem_root / "vocals.wav").write_bytes(_wav_bytes())
        (stem_root / "no_vocals.wav").write_bytes(_wav_bytes())

    async def run() -> None:
        source = tmp_path / "song.wav"
        source.write_bytes(_wav_bytes())
        monkeypatch.setattr("capabilities.singing.separation.demucs.run_trusted_process", fake_run)
        separator = DemucsSeparator(DemucsBackendConfig(_runtime(tmp_path)))
        result = await separator.separate(SongAsset("song-01", source), asyncio.Event())
        assert result.vocal.sample_rate == result.instrumental.sample_rate == 44100
        assert result.vocal.duration_ms == result.instrumental.duration_ms == 100

    asyncio.run(run())
