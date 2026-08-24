"""Private, trusted subprocess primitives for optional local SVC runtimes.

Nothing in this module is populated from a public ``CoverRequest``.  Runtime
locations, models, references, and executable paths are application-owned
composition concerns and stay in ignored local configuration.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import os
import re
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

from ..assets import VocalArtifact
from ..errors import SingingError


@dataclass(frozen=True)
class TrustedRuntimeConfig:
    """Fixed local runtime process settings, never a public model."""

    python_executable: Path
    working_directory: Path
    timeout_seconds: float = 180.0
    max_output_bytes: int = 16 * 1024 * 1024
    max_output_duration_ms: int = 70_000
    git_executable: str | Path = "git"

    def __post_init__(self) -> None:
        if not self.python_executable.is_absolute() or not self.working_directory.is_absolute():
            raise ValueError("trusted_runtime_paths_must_be_absolute")
        if not 1.0 <= self.timeout_seconds <= 600.0:
            raise ValueError("invalid_runtime_timeout")
        if not 44_100 <= self.max_output_bytes <= 64 * 1024 * 1024:
            raise ValueError("invalid_max_output_bytes")
        if not 1_000 <= self.max_output_duration_ms <= 120_000:
            raise ValueError("invalid_max_output_duration")


@dataclass(frozen=True)
class RuntimeModelDependency:
    """One approved local model/cache component required before offline inference."""

    component_id: str
    local_path: Path
    required: bool = True
    source_revision: str | None = None
    expected_size_bytes: int | None = None

    def __post_init__(self) -> None:
        if re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", self.component_id) is None:
            raise ValueError("invalid_dependency_component")
        if not self.local_path.is_absolute():
            raise ValueError("dependency_path_must_be_absolute")
        if self.source_revision is not None and not _is_full_sha(self.source_revision):
            raise ValueError("invalid_dependency_revision")
        if self.expected_size_bytes is not None and self.expected_size_bytes <= 0:
            raise ValueError("invalid_expected_dependency_size")


class TrustedProcessFailure(RuntimeError):
    """Internal process outcome; callers map it to their bounded public error."""

    def __init__(self, kind: str) -> None:
        self.kind = kind
        super().__init__(kind)


class RuntimePreflightFailure(RuntimeError):
    """Internal source/dependency validation outcome without a private path."""

    def __init__(self, kind: str) -> None:
        self.kind = kind
        super().__init__(kind)


def require_absolute_file(path: Path, *, label: str) -> None:
    if not path.is_absolute() or not path.is_file():
        raise SingingError("engine_unavailable")


async def run_trusted_process(
    command: Sequence[str | Path],
    runtime: TrustedRuntimeConfig,
    cancelled: asyncio.Event,
    *,
    environment: Mapping[str, str] | None = None,
) -> None:
    """Run a fixed argv process without retaining raw backend output."""

    if cancelled.is_set():
        raise SingingError("cancelled")
    process_environment = os.environ.copy()
    if environment is not None:
        process_environment.update(environment)
    try:
        process = await asyncio.create_subprocess_exec(
            *(str(item) for item in command),
            cwd=str(runtime.working_directory),
            env=process_environment,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except OSError as error:
        raise TrustedProcessFailure("spawn") from error
    wait_task = asyncio.create_task(process.wait())
    cancel_task = asyncio.create_task(cancelled.wait())
    try:
        done, pending = await asyncio.wait(
            {wait_task, cancel_task},
            timeout=runtime.timeout_seconds,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if not done:
            await _terminate(process)
            raise SingingError("timeout")
        if cancel_task in done:
            await _terminate(process)
            raise SingingError("cancelled")
        if wait_task.result() != 0:
            raise TrustedProcessFailure("exit")
    except asyncio.CancelledError:
        await _terminate(process)
        raise
    finally:
        for task in (wait_task, cancel_task):
            if not task.done():
                task.cancel()
        await asyncio.gather(wait_task, cancel_task, return_exceptions=True)


async def verify_runtime_revision(runtime: TrustedRuntimeConfig, expected_revision: str) -> str:
    """Require a clean tracked source checkout at an exact immutable revision."""

    if not _is_full_sha(expected_revision):
        raise RuntimePreflightFailure("invalid_revision")
    actual = await _git_output(runtime, "rev-parse", "HEAD")
    if actual != expected_revision:
        raise RuntimePreflightFailure("revision_mismatch")
    dirty = await _git_output(runtime, "status", "--porcelain", "--untracked-files=no")
    if dirty:
        raise RuntimePreflightFailure("dirty_source")
    return actual


async def preflight_runtime(
    runtime: TrustedRuntimeConfig,
    expected_revision: str,
    dependencies: Sequence[RuntimeModelDependency],
) -> str:
    """Verify source provenance and all required local model/cache components."""

    for dependency in dependencies:
        if dependency.required and not dependency.local_path.exists():
            raise RuntimePreflightFailure("dependency_missing")
    return await verify_runtime_revision(runtime, expected_revision)


async def _git_output(runtime: TrustedRuntimeConfig, *arguments: str) -> str:
    try:
        process = await asyncio.create_subprocess_exec(
            str(runtime.git_executable),
            "-C",
            str(runtime.working_directory),
            *arguments,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except OSError as error:
        raise RuntimePreflightFailure("revision_check_failed") from error
    try:
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=10.0)
    except asyncio.TimeoutError as error:
        await _terminate(process)
        raise RuntimePreflightFailure("revision_check_failed") from error
    except asyncio.CancelledError:
        await _terminate(process)
        raise
    if process.returncode != 0:
        raise RuntimePreflightFailure("revision_check_failed")
    value = stdout.decode("ascii", errors="ignore").strip()
    if len(value) > 80:
        raise RuntimePreflightFailure("revision_check_failed")
    return value


def offline_environment() -> dict[str, str]:
    """Approved process-local policy; host environment is inherited unchanged."""

    return {
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "HF_DATASETS_OFFLINE": "1",
    }


async def _terminate(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=5.0)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()


def write_private_input(directory: Path, vocal: VocalArtifact) -> Path:
    """Materialize an in-memory WAV only inside an adapter-owned temp directory."""

    source = directory / "source.wav"
    source.write_bytes(vocal.audio)
    _validate_wav(source, expected_sample_rate=vocal.sample_rate, runtime=None)
    return source


def read_validated_wav(
    path: Path,
    runtime: TrustedRuntimeConfig,
    *,
    expected_sample_rate: int | None,
) -> VocalArtifact:
    _validate_wav(path, expected_sample_rate=expected_sample_rate, runtime=runtime)
    audio = path.read_bytes()
    with contextlib.closing(wave.open(io.BytesIO(audio), "rb")) as reader:
        duration_ms = round(reader.getnframes() * 1000 / reader.getframerate())
        return VocalArtifact(
            audio=audio,
            duration_ms=duration_ms,
            sample_rate=reader.getframerate(),
        )


def _validate_wav(
    path: Path,
    *,
    expected_sample_rate: int | None,
    runtime: TrustedRuntimeConfig | None,
) -> None:
    try:
        size = path.stat().st_size
        if runtime is not None and (size <= 44 or size > runtime.max_output_bytes):
            raise ValueError("invalid_size")
        with contextlib.closing(wave.open(str(path), "rb")) as reader:
            if reader.getcomptype() != "NONE" or reader.getsampwidth() != 2:
                raise ValueError("unsupported_wav_encoding")
            if reader.getnchannels() not in {1, 2} or reader.getnframes() <= 0:
                raise ValueError("invalid_wav_shape")
            if expected_sample_rate is not None and reader.getframerate() != expected_sample_rate:
                raise ValueError("unexpected_sample_rate")
            duration_ms = round(reader.getnframes() * 1000 / reader.getframerate())
            if runtime is not None and duration_ms > runtime.max_output_duration_ms:
                raise ValueError("output_too_long")
    except (OSError, EOFError, wave.Error, ValueError) as error:
        raise SingingError("conversion_failed") from error


def find_only_wav(directory: Path, *, preferred_name: str | None = None) -> Path:
    candidates = sorted(path for path in directory.rglob("*.wav") if path.is_file())
    if preferred_name is not None:
        candidates = [path for path in candidates if path.name == preferred_name]
    if len(candidates) != 1:
        raise SingingError("conversion_failed")
    return candidates[0]


def _is_full_sha(value: str) -> bool:
    return re.fullmatch(r"[0-9a-f]{40}", value) is not None
