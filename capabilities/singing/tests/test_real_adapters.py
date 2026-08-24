from __future__ import annotations

import asyncio
import inspect
import io
import os
import subprocess
import sys
import wave
from pathlib import Path

import pytest

from capabilities.singing.assets import VocalArtifact
from capabilities.singing.assets import SongAsset
from capabilities.singing.bakeoff_cli import main as bakeoff_main
from capabilities.singing.engines.base import ConversionOptions
from capabilities.singing.engines.rvc import RVCAdapter, RVCBackendConfig
from capabilities.singing.engines.runtime import (
    RuntimeModelDependency,
    RuntimePreflightFailure,
    TrustedRuntimeConfig,
    preflight_runtime,
)
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


async def _verified_runtime(*_args, **_kwargs) -> str:
    return "a" * 40


def _seed_dependencies(tmp_path: Path) -> tuple[RuntimeModelDependency, ...]:
    dependencies = []
    for component in ("rmvpe", "campplus", "bigvgan", "whisper"):
        path = tmp_path / component
        path.write_bytes(b"ready")
        dependencies.append(RuntimeModelDependency(component, path))
    return tuple(dependencies)


def _rvc_dependencies(tmp_path: Path) -> tuple[RuntimeModelDependency, ...]:
    assets = tmp_path / "rvc-assets"
    assets.mkdir(exist_ok=True)
    return (RuntimeModelDependency("rvc_runtime_assets", assets),)


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


def test_seedvc_trusted_config_runs_fixed_backend_and_returns_valid_wav(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def run() -> None:
        script = _write_backend(tmp_path, _writing_backend())
        checkpoint = tmp_path / "model.pth"
        model_config = tmp_path / "model.yml"
        reference = tmp_path / "reference.wav"
        checkpoint.write_bytes(b"model")
        model_config.write_text("model: seed")
        reference.write_bytes(_wav_bytes())
        monkeypatch.setattr("capabilities.singing.engines.seed_vc.preflight_runtime", _verified_runtime)
        adapter = SeedVCAdapter(
            SeedVCBackendConfig(
                _runtime(tmp_path), script, checkpoint, model_config, reference, _seed_dependencies(tmp_path)
            ),
            revision="pin-1",
        )
        result = await adapter.convert(VocalArtifact(_wav_bytes(), 100, 44100), "yogurt", _options(), asyncio.Event())
        assert result.media_type == "audio/wav"
        assert result.sample_rate == 44100
        assert result.duration_ms == 100

    asyncio.run(run())


@pytest.mark.parametrize("malformed,oversized", [(True, False), (False, True)])
def test_real_adapter_rejects_malformed_or_oversized_wav(
    tmp_path: Path, malformed: bool, oversized: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def run() -> None:
        script = _write_backend(tmp_path, _writing_backend(malformed=malformed, oversized=oversized))
        model = tmp_path / "yogurt.pth"
        model.write_bytes(b"model")
        monkeypatch.setattr("capabilities.singing.engines.rvc.preflight_runtime", _verified_runtime)
        adapter = RVCAdapter(
            RVCBackendConfig(
                _runtime(tmp_path, max_output_bytes=44_100), script, model, dependencies=_rvc_dependencies(tmp_path)
            ),
            revision="pin-1",
        )
        with pytest.raises(SingingError, match="conversion_failed"):
            await adapter.convert(VocalArtifact(_wav_bytes(), 100, 44100), "yogurt", _options(), asyncio.Event())

    asyncio.run(run())


def test_backend_exit_and_cancellation_are_bounded_and_redacted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def run() -> None:
        failing = _write_backend(tmp_path, "import sys; print('/private/model', file=sys.stderr); raise SystemExit(2)")
        model = tmp_path / "yogurt.pth"
        model.write_bytes(b"model")
        monkeypatch.setattr("capabilities.singing.engines.rvc.preflight_runtime", _verified_runtime)
        adapter = RVCAdapter(
            RVCBackendConfig(_runtime(tmp_path), failing, model, dependencies=_rvc_dependencies(tmp_path)),
            revision="pin-1",
        )
        with pytest.raises(SingingError, match="conversion_failed") as failure:
            await adapter.convert(VocalArtifact(_wav_bytes(), 100, 44100), "yogurt", _options(), asyncio.Event())
        assert "/private" not in str(failure.value)

        sleeping = _write_backend(tmp_path, "import time; time.sleep(30)")
        adapter = RVCAdapter(
            RVCBackendConfig(_runtime(tmp_path), sleeping, model, dependencies=_rvc_dependencies(tmp_path)),
            revision="pin-1",
        )
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
    async def fake_run(command, runtime, cancelled, *, environment) -> None:
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
        monkeypatch.setattr("capabilities.singing.separation.demucs.preflight_runtime", _verified_runtime)
        model = tmp_path / "demucs-model"
        model.write_bytes(b"ready")
        separator = DemucsSeparator(
            DemucsBackendConfig(_runtime(tmp_path), "a" * 40, RuntimeModelDependency("demucs", model))
        )
        result = await separator.separate(SongAsset("song-01", source), asyncio.Event())
        assert result.vocal.sample_rate == result.instrumental.sample_rate == 44100
        assert result.vocal.duration_ms == result.instrumental.duration_ms == 100

    asyncio.run(run())


def _init_git_repository(tmp_path: Path) -> str:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "source.py").write_text("ready = True\n")
    subprocess.run(["git", "add", "source.py"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "init"],
        cwd=tmp_path,
        check=True,
    )
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=tmp_path, text=True).strip()


def test_runtime_revision_match_mismatch_dirty_and_check_failure_are_bounded(tmp_path: Path) -> None:
    async def run() -> None:
        revision = _init_git_repository(tmp_path)
        runtime = _runtime(tmp_path)
        dependency = tmp_path / "dependency"
        dependency.write_bytes(b"ready")
        assert await preflight_runtime(runtime, revision, (RuntimeModelDependency("rmvpe", dependency),)) == revision
        with pytest.raises(RuntimePreflightFailure, match="revision_mismatch"):
            await preflight_runtime(runtime, "b" * 40, ())
        (tmp_path / "source.py").write_text("changed = True\n")
        with pytest.raises(RuntimePreflightFailure, match="dirty_source"):
            await preflight_runtime(runtime, revision, ())
        unavailable_git = TrustedRuntimeConfig(
            python_executable=Path(sys.executable).resolve(),
            working_directory=tmp_path,
            git_executable="missing-yogurt-git",
        )
        with pytest.raises(RuntimePreflightFailure, match="revision_check_failed"):
            await preflight_runtime(unavailable_git, revision, ())

    asyncio.run(run())


def test_runtime_revision_mismatch_blocks_before_backend_launch(tmp_path: Path) -> None:
    async def run() -> None:
        _init_git_repository(tmp_path)
        marker = tmp_path / "backend-started"
        script = _write_backend(tmp_path, f"from pathlib import Path; Path({str(marker)!r}).write_text('started')")
        checkpoint, model_config, reference = (tmp_path / "model.pth", tmp_path / "model.yml", tmp_path / "ref.wav")
        checkpoint.write_bytes(b"model")
        model_config.write_text("model: seed")
        reference.write_bytes(_wav_bytes())
        adapter = SeedVCAdapter(
            SeedVCBackendConfig(
                _runtime(tmp_path), script, checkpoint, model_config, reference, _seed_dependencies(tmp_path)
            ),
            revision="b" * 40,
        )
        with pytest.raises(SingingError, match="engine_unavailable"):
            await adapter.convert(VocalArtifact(_wav_bytes(), 100, 44100), "yogurt", _options(), asyncio.Event())
        assert not marker.exists()

    asyncio.run(run())


def test_seedvc_dependency_preflight_and_offline_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def run() -> None:
        marker = tmp_path / "offline-marker"
        script = _write_backend(
            tmp_path,
            _writing_backend()
            + f"\npathlib.Path({str(marker)!r}).write_text('|'.join([__import__('os').environ.get(key, '') for key in ('HF_HUB_OFFLINE', 'TRANSFORMERS_OFFLINE', 'HF_DATASETS_OFFLINE')]))\n",
        )
        checkpoint, model_config, reference = (tmp_path / "model.pth", tmp_path / "model.yml", tmp_path / "ref.wav")
        checkpoint.write_bytes(b"model")
        model_config.write_text("model: seed")
        reference.write_bytes(_wav_bytes())
        dependencies = _seed_dependencies(tmp_path)
        monkeypatch.setattr("capabilities.singing.engines.seed_vc.preflight_runtime", _verified_runtime)
        adapter = SeedVCAdapter(
            SeedVCBackendConfig(_runtime(tmp_path), script, checkpoint, model_config, reference, dependencies),
            revision="a" * 40,
        )
        await adapter.convert(VocalArtifact(_wav_bytes(), 100, 44100), "yogurt", _options(), asyncio.Event())
        assert marker.read_text() == "1|1|1"
        missing = tuple(
            RuntimeModelDependency(item.component_id, tmp_path / "missing-rmvpe")
            if item.component_id == "rmvpe"
            else item
            for item in dependencies
        )
        missing_adapter = SeedVCAdapter(
            SeedVCBackendConfig(_runtime(tmp_path), script, checkpoint, model_config, reference, missing),
            revision="a" * 40,
        )
        monkeypatch.setattr(
            "capabilities.singing.engines.seed_vc.preflight_runtime",
            preflight_runtime,
        )
        with pytest.raises(SingingError, match="engine_unavailable"):
            await missing_adapter.preflight()

    asyncio.run(run())


def test_external_task_cancel_terminates_child_process(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        started = tmp_path / "started"
        terminated = tmp_path / "terminated"
        script = _write_backend(
            tmp_path,
            f"""
import os
import pathlib
import signal
import sys
import time
pathlib.Path({str(started)!r}).write_text(str(os.getpid()))
def stop(*_args):
    pathlib.Path({str(terminated)!r}).write_text('terminated')
    raise SystemExit(0)
signal.signal(signal.SIGTERM, stop)
time.sleep(30)
""",
        )
        model = tmp_path / "yogurt.pth"
        model.write_bytes(b"model")
        monkeypatch.setattr("capabilities.singing.engines.rvc.preflight_runtime", _verified_runtime)
        adapter = RVCAdapter(
            RVCBackendConfig(_runtime(tmp_path), script, model, dependencies=_rvc_dependencies(tmp_path)),
            revision="a" * 40,
        )
        task = asyncio.create_task(
            adapter.convert(VocalArtifact(_wav_bytes(), 100, 44100), "yogurt", _options(), asyncio.Event())
        )
        for _ in range(40):
            if started.exists():
                break
            await asyncio.sleep(0.01)
        assert started.exists()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert terminated.read_text() == "terminated"

    asyncio.run(run())


def test_spawn_failure_and_demucs_failure_mapping_are_bounded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def run() -> None:
        model = tmp_path / "yogurt.pth"
        model.write_bytes(b"model")
        unavailable = TrustedRuntimeConfig(
            python_executable=Path("/private/yogurt/missing-python"), working_directory=tmp_path
        )
        cli = tmp_path / "cli.py"
        cli.write_text("raise SystemExit(0)\n")
        monkeypatch.setattr("capabilities.singing.engines.rvc.preflight_runtime", _verified_runtime)
        adapter = RVCAdapter(
            RVCBackendConfig(unavailable, cli, model, dependencies=_rvc_dependencies(tmp_path)),
            revision="a" * 40,
        )
        with pytest.raises(SingingError, match="engine_unavailable") as failure:
            await adapter.convert(VocalArtifact(_wav_bytes(), 100, 44100), "yogurt", _options(), asyncio.Event())
        assert "/private" not in str(failure.value)

        source = tmp_path / "song.wav"
        source.write_bytes(_wav_bytes())
        cache = tmp_path / "demucs-model"
        cache.write_bytes(b"ready")
        config = DemucsBackendConfig(_runtime(tmp_path), "a" * 40, RuntimeModelDependency("demucs", cache))
        separator = DemucsSeparator(config)
        monkeypatch.setattr("capabilities.singing.separation.demucs.preflight_runtime", _verified_runtime)

        async def exited(*_args, **_kwargs):
            from capabilities.singing.engines.runtime import TrustedProcessFailure

            raise TrustedProcessFailure("exit")

        monkeypatch.setattr("capabilities.singing.separation.demucs.run_trusted_process", exited)
        with pytest.raises(SingingError, match="separation_failed"):
            await separator.separate(SongAsset("song-01", source), asyncio.Event())
        with pytest.raises(SingingError, match="separator_unavailable"):
            await DemucsSeparator().separate(SongAsset("song-01", source), asyncio.Event())

    asyncio.run(run())


def test_demucs_missing_stems_timeout_cancel_and_model_preflight_are_bounded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def run() -> None:
        source = tmp_path / "song.wav"
        source.write_bytes(_wav_bytes())
        cache = tmp_path / "demucs-model"
        cache.write_bytes(b"ready")
        config = DemucsBackendConfig(_runtime(tmp_path), "a" * 40, RuntimeModelDependency("demucs", cache))
        separator = DemucsSeparator(config)
        monkeypatch.setattr("capabilities.singing.separation.demucs.preflight_runtime", _verified_runtime)

        async def no_stems(*_args, **_kwargs):
            return None

        monkeypatch.setattr("capabilities.singing.separation.demucs.run_trusted_process", no_stems)
        with pytest.raises(SingingError, match="separation_failed"):
            await separator.separate(SongAsset("song-01", source), asyncio.Event())

        async def timed_out(*_args, **_kwargs):
            raise SingingError("timeout")

        monkeypatch.setattr("capabilities.singing.separation.demucs.run_trusted_process", timed_out)
        with pytest.raises(SingingError, match="timeout"):
            await separator.separate(SongAsset("song-01", source), asyncio.Event())

        async def cancelled(*_args, **_kwargs):
            raise SingingError("cancelled")

        monkeypatch.setattr("capabilities.singing.separation.demucs.run_trusted_process", cancelled)
        with pytest.raises(SingingError, match="cancelled"):
            await separator.separate(SongAsset("song-01", source), asyncio.Event())

        missing_config = DemucsBackendConfig(
            _runtime(tmp_path), "a" * 40, RuntimeModelDependency("demucs", tmp_path / "missing-model")
        )
        monkeypatch.setattr(
            "capabilities.singing.separation.demucs.preflight_runtime", preflight_runtime
        )
        with pytest.raises(SingingError, match="separator_unavailable"):
            await DemucsSeparator(missing_config).preflight()

    asyncio.run(run())


def test_cli_preflight_rejects_invalid_input_without_exposing_config(capsys: pytest.CaptureFixture[str]) -> None:
    result = bakeoff_main(
        ["--sample-id", "../private", "--engine", "seed_vc", "--language", "zh", "--preflight-only"]
    )
    captured = capsys.readouterr()
    assert result == 2
    assert captured.out == '{"success": false, "error_code": "invalid_request"}\n'
    assert "private" not in captured.out


def test_cli_config_failure_is_bounded_before_any_backend(capsys: pytest.CaptureFixture[str]) -> None:
    result = bakeoff_main(
        ["--sample-id", "zh-proto", "--engine", "seed_vc", "--language", "zh", "--preflight-only"]
    )
    captured = capsys.readouterr()
    assert result == 2
    assert captured.out == '{"success": false, "error_code": "engine_unavailable"}\n'
