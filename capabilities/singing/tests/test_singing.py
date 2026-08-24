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

    async def separate(self, asset, cancelled) -> SeparatedSong:
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

    async def convert(self, vocal, profile, options, cancelled) -> VocalArtifact:
        self.options = options
        if self.error:
            raise self.error
        return VocalArtifact(b"yogurt-vocal", vocal.duration_ms, vocal.sample_rate)


class FakeMixer:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error

    async def mix(self, vocal, instrumental, request, cancelled) -> VocalArtifact:
        if self.error:
            raise self.error
        return VocalArtifact(b"mixed", vocal.duration_ms, vocal.sample_rate)


def _service(
    separator: FakeSeparator | None = None,
    engine: FakeEngine | None = None,
    mixer: FakeMixer | None = None,
) -> SingingService:
    registry = SingingAssetRegistry(
        {"song-01": SongAsset("song-01", Path("/private/yogurt/song.wav"))}
    )
    return SingingService(
        registry,
        separator or FakeSeparator(),
        engine or FakeEngine(),
        RemixService(mixer or FakeMixer()),
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
        result = await service.cover(CoverRequest(asset_id="song-01", transpose_semitones=2))
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
        assert engine.options.transpose_semitones == 2
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
