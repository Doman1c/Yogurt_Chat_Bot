from __future__ import annotations

import asyncio
import time
from typing import Mapping

from .assets import VocalArtifact
from .engines.base import ConversionOptions, SingingConversionEngine
from .errors import SingingError
from .schemas import BakeoffRecord, SingingProfile


class SingingBakeoffHarness:
    """Run trusted, preconfigured engines over one private in-memory vocal sample."""

    def __init__(self, engines: Mapping[str, SingingConversionEngine]) -> None:
        self._engines = dict(engines)

    async def run(
        self,
        *,
        sample_id: str,
        language: str,
        vocal: VocalArtifact,
        profile: SingingProfile,
        options: ConversionOptions,
    ) -> tuple[BakeoffRecord, ...]:
        if language not in {"zh", "ja", "en"}:
            raise ValueError("invalid_language")
        records: list[BakeoffRecord] = []
        for engine in self._engines.values():
            started_at = time.monotonic()
            try:
                converted = await engine.convert(vocal, profile, options, asyncio.Event())
            except SingingError as error:
                records.append(
                    self._record(engine, sample_id, language, options, started_at, False, error.code)
                )
            except Exception:
                records.append(
                    self._record(
                        engine, sample_id, language, options, started_at, False, "conversion_failed"
                    )
                )
            else:
                records.append(
                    self._record(
                        engine,
                        sample_id,
                        language,
                        options,
                        started_at,
                        True,
                        duration_ms=converted.duration_ms,
                        sample_rate=converted.sample_rate,
                    )
                )
        return tuple(records)

    @staticmethod
    def _record(
        engine: SingingConversionEngine,
        sample_id: str,
        language: str,
        options: ConversionOptions,
        started_at: float,
        success: bool,
        error_code: str | None = None,
        duration_ms: int | None = None,
        sample_rate: int | None = None,
    ) -> BakeoffRecord:
        return BakeoffRecord(
            engine=engine.name,
            engine_version=engine.version,
            sample_id=sample_id,
            language=language,
            transpose_semitones=options.transpose_semitones,
            wall_time_ms=round((time.monotonic() - started_at) * 1000),
            output_duration_ms=duration_ms,
            sample_rate=sample_rate,
            success=success,
            error_code=error_code,
        )
