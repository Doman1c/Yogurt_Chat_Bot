from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from pydantic import ValidationError

from capabilities.singing.assets import (
    InstrumentalArtifact,
    SeparatedSong,
    SingingAssetRegistry,
    SongAsset,
    VocalArtifact,
)
from capabilities.singing.bakeoff import SingingBakeoffHarness
from capabilities.singing.engines.base import ConversionOptions
from capabilities.singing.errors import SingingError
from capabilities.singing.remix.service import RemixService
from capabilities.singing.schemas import CoverRequest
from capabilities.singing.service import SingingService


class FakeSeparator:
    def __init__(self, error: Exception | None = None, *, block: bool = False) -> None:
        self.error = error
        self.block = block
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        if not block:
            self.release.set()
        self.calls = 0

    async def separate(self, asset, cancelled) -> SeparatedSong:
        self.calls += 1
        self.started.set()
        await self.release.wait()
        if self.error:
            raise self.error
        return SeparatedSong(
            vocal=VocalArtifact(b"vocal", 1000, 44100),
            instrumental=InstrumentalArtifact(b"instrumental", 1000, 44100),
        )


class FakeEngine:
    name = "fake"
    version = "test-1"

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.options = None
        self.calls = 0

    async def convert(self, vocal, profile, options, cancelled) -> VocalArtifact:
        self.calls += 1
        self.options = options
        if self.error:
            raise self.error
        return VocalArtifact(b"yogurt-vocal", vocal.duration_ms, vocal.sample_rate)


class FakeMixer:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls = 0

    async def mix(self, vocal, instrumental, request, cancelled) -> VocalArtifact:
        self.calls += 1
        if self.error:
            raise self.error
        return VocalArtifact(b"mixed", vocal.duration_ms, vocal.sample_rate)


def _service(
    separator: FakeSeparator | None = None,
    engine: FakeEngine | None = None,
    mixer: FakeMixer | None = None,
    *,
    max_concurrent_jobs: int = 1,
    max_pending_jobs: int = 4,
    supports_synchronized_transpose: bool = False,
) -> SingingService:
    registry = SingingAssetRegistry(
        {"song-01": SongAsset("song-01", Path("/private/yogurt/song.wav"))}
    )
    return SingingService(
        registry,
        separator or FakeSeparator(),
        engine or FakeEngine(),
        RemixService(
            mixer or FakeMixer(), supports_synchronized_transpose=supports_synchronized_transpose
        ),
        max_concurrent_jobs=max_concurrent_jobs,
        max_pending_jobs=max_pending_jobs,
    )


def _event_kinds(service: SingingService, job_id: str) -> list[str]:
    return [event.kind for event in service.events() if event.job_id == job_id]


def test_cover_request_is_bounded_and_forbids_extra_controls() -> None:
    request = CoverRequest(asset_id="song-01", transpose_semitones=-12)
    assert request.singing_profile == "yogurt"
    with pytest.raises(ValidationError):
        CoverRequest(asset_id="../song")
    with pytest.raises(ValidationError):
        CoverRequest(asset_id="song-01", transpose_semitones=13)
    with pytest.raises(ValidationError):
        CoverRequest(asset_id="song-01", engine_url="http://example.invalid")
    assert "path" not in CoverRequest.model_fields
    assert "checkpoint" not in CoverRequest.model_fields
    assert "command" not in CoverRequest.model_fields


def test_registry_rejects_untrusted_ids_and_never_returns_a_path_publicly() -> None:
    registry = SingingAssetRegistry()
    with pytest.raises(ValueError):
        registry.register("../song", SongAsset("../song", Path("/private/song.wav")))
    with pytest.raises(SingingError, match="unknown_asset"):
        registry.resolve("https://example.invalid/song.wav")


def test_unknown_asset_is_a_bounded_service_failure() -> None:
    async def run() -> None:
        service = _service()
        result = await service.cover(CoverRequest(asset_id="missing-song"))
        assert result.success is False
        assert result.error_code == "unknown_asset"
        assert _event_kinds(service, result.job_id) == ["queued", "failed"]
        assert "/private" not in result.model_dump_json()

    asyncio.run(run())


def test_successful_pipeline_has_one_ordered_lifecycle_and_safe_result() -> None:
    async def run() -> None:
        engine = FakeEngine()
        service = _service(engine=engine)
        result = await service.cover(CoverRequest(asset_id="song-01"))
        assert result.success is True
        assert result.audio == b"mixed"
        assert result.error_code is None
        assert _event_kinds(service, result.job_id) == [
            "queued",
            "asset_ready",
            "separation_started",
            "separation_finished",
            "conversion_started",
            "conversion_finished",
            "remix_started",
            "cover_ready",
        ]
        assert engine.options.transpose_semitones == 0
        assert "/private" not in result.model_dump_json()
        assert "/private" not in "".join(event.model_dump_json() for event in service.events())

    asyncio.run(run())


@pytest.mark.parametrize(
    ("separator_error", "engine_error", "mixer_error", "code"),
    [
        (RuntimeError("private /tmp/source.wav"), None, None, "separation_failed"),
        (None, RuntimeError("private checkpoint"), None, "conversion_failed"),
        (None, None, RuntimeError("private ffmpeg"), "remix_failed"),
    ],
)
def test_stage_failures_are_bounded(
    separator_error: Exception | None,
    engine_error: Exception | None,
    mixer_error: Exception | None,
    code: str,
) -> None:
    async def run() -> None:
        service = _service(
            separator=FakeSeparator(separator_error),
            engine=FakeEngine(engine_error),
            mixer=FakeMixer(mixer_error),
        )
        result = await service.cover(CoverRequest(asset_id="song-01"))
        assert result.success is False
        assert result.error_code == code
        assert _event_kinds(service, result.job_id)[-1] == "failed"
        assert "private" not in result.model_dump_json()

    asyncio.run(run())


def test_cancellation_discards_later_separator_output_with_one_terminal_event() -> None:
    async def run() -> None:
        separator = FakeSeparator(block=True)
        service = _service(separator=separator)
        handle = await service.submit(CoverRequest(asset_id="song-01"))
        await separator.started.wait()
        assert handle.cancel() is True
        separator.release.set()
        result = await handle.wait()
        assert result.success is False
        assert result.error_code == "cancelled"
        assert _event_kinds(service, handle.job_id) == [
            "queued",
            "asset_ready",
            "separation_started",
            "cancelled",
        ]

    asyncio.run(run())


def test_waiting_job_cancelled_before_capacity_never_runs_pipeline() -> None:
    async def run() -> None:
        separator = FakeSeparator(block=True)
        engine = FakeEngine()
        mixer = FakeMixer()
        service = _service(separator, engine, mixer, max_pending_jobs=1)
        active = await service.submit(CoverRequest(asset_id="song-01"))
        await separator.started.wait()
        waiting = await service.submit(CoverRequest(asset_id="song-01"))
        assert _event_kinds(service, waiting.job_id) == ["queued"]
        assert waiting.cancel() is True
        result = await waiting.wait()
        assert result.error_code == "cancelled"
        assert separator.calls == 1
        assert engine.calls == 0
        assert mixer.calls == 0
        assert _event_kinds(service, waiting.job_id) == ["queued", "cancelled"]
        separator.release.set()
        assert (await active.wait()).success is True

    asyncio.run(run())


def test_second_job_waits_until_first_releases_capacity() -> None:
    async def run() -> None:
        separator = FakeSeparator(block=True)
        service = _service(separator, max_pending_jobs=1)
        first = await service.submit(CoverRequest(asset_id="song-01"))
        await separator.started.wait()
        second = await service.submit(CoverRequest(asset_id="song-01"))
        assert _event_kinds(service, second.job_id) == ["queued"]
        separator.release.set()
        assert (await first.wait()).success is True
        assert (await second.wait()).success is True
        assert _event_kinds(service, second.job_id) == [
            "queued",
            "asset_ready",
            "separation_started",
            "separation_finished",
            "conversion_started",
            "conversion_finished",
            "remix_started",
            "cover_ready",
        ]

    asyncio.run(run())


def test_pending_queue_is_bounded() -> None:
    async def run() -> None:
        separator = FakeSeparator(block=True)
        service = _service(separator, max_pending_jobs=1)
        active = await service.submit(CoverRequest(asset_id="song-01"))
        await separator.started.wait()
        waiting = await service.submit(CoverRequest(asset_id="song-01"))
        rejected = await service.submit(CoverRequest(asset_id="song-01"))
        assert (await rejected.wait()).error_code == "queue_full"
        assert _event_kinds(service, rejected.job_id) == ["failed"]
        assert waiting.cancel() is True
        assert (await waiting.wait()).error_code == "cancelled"
        separator.release.set()
        assert (await active.wait()).success is True

    asyncio.run(run())


def test_mixed_transpose_is_guarded_without_synchronized_support() -> None:
    async def run() -> None:
        separator = FakeSeparator()
        engine = FakeEngine()
        mixer = FakeMixer()
        service = _service(separator, engine, mixer)
        result = await service.cover(CoverRequest(asset_id="song-01", transpose_semitones=2))
        assert result.success is False
        assert result.error_code == "unsupported_transpose"
        assert separator.calls == engine.calls == mixer.calls == 0
        assert _event_kinds(service, result.job_id) == ["failed"]

    asyncio.run(run())


def test_vocal_only_and_supported_mixed_transpose_are_allowed() -> None:
    async def run() -> None:
        vocal_engine = FakeEngine()
        vocal_service = _service(engine=vocal_engine)
        vocal = await vocal_service.cover(
            CoverRequest(asset_id="song-01", output="vocal", transpose_semitones=2)
        )
        assert vocal.success is True
        assert vocal_engine.options.transpose_semitones == 2

        mixed_engine = FakeEngine()
        mixed_service = _service(
            engine=mixed_engine, supports_synchronized_transpose=True
        )
        mixed = await mixed_service.cover(CoverRequest(asset_id="song-01", transpose_semitones=2))
        assert mixed.success is True
        assert mixed_engine.options.transpose_semitones == 2

    asyncio.run(run())


def test_each_job_has_exactly_one_terminal_event() -> None:
    async def run() -> None:
        service = _service()
        completed = await service.cover(CoverRequest(asset_id="song-01"))
        rejected = await service.cover(CoverRequest(asset_id="song-01", transpose_semitones=1))
        terminal = {"cover_ready", "failed", "cancelled"}
        for result in (completed, rejected):
            kinds = _event_kinds(service, result.job_id)
            assert len([kind for kind in kinds if kind in terminal]) == 1

    asyncio.run(run())


def test_bakeoff_records_success_and_bounded_failure() -> None:
    async def run() -> None:
        failure = FakeEngine(SingingError("engine_unavailable"))
        failure.name = "unavailable"
        harness = SingingBakeoffHarness({"fake": FakeEngine(), "bad": failure})
        records = await harness.run(
            sample_id="zh-sustain-01",
            language="zh",
            vocal=VocalArtifact(b"vocal", 1000, 44100),
            profile="yogurt",
            options=ConversionOptions(0, True, True, "medium"),
        )
        assert [record.success for record in records] == [True, False]
        assert records[1].error_code == "engine_unavailable"

    asyncio.run(run())
