from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass
from typing import Optional

from .assets import SingingAssetRegistry, VocalArtifact
from .engines.base import ConversionOptions, SingingConversionEngine
from .errors import SingingError, SingingErrorCode
from .events import SingingEvent
from .remix.service import RemixService
from .schemas import CoverRequest, SingingResult
from .separation.base import SourceSeparator


@dataclass
class _CoverJob:
    job_id: str
    request: CoverRequest
    completion: "asyncio.Future[SingingResult]"
    cancelled: asyncio.Event
    started_at: float


class SingingHandle:
    """Opaque asynchronous handle for one registered-song cover job."""

    def __init__(self, job: _CoverJob) -> None:
        self.job_id = job.job_id
        self._job = job

    def cancel(self) -> bool:
        if self._job.completion.done():
            return False
        self._job.cancelled.set()
        return True

    async def wait(self) -> SingingResult:
        return await self._job.completion


class SingingService:
    """Safe Cover Mode coordinator composed from trusted local implementations."""

    def __init__(
        self,
        assets: SingingAssetRegistry,
        separator: SourceSeparator,
        engine: SingingConversionEngine,
        remixer: RemixService,
    ) -> None:
        self._assets = assets
        self._separator = separator
        self._engine = engine
        self._remixer = remixer
        self._events: list[SingingEvent] = []

    def events(self) -> tuple[SingingEvent, ...]:
        return tuple(self._events)

    async def submit(self, request: CoverRequest) -> SingingHandle:
        job = _CoverJob(
            job_id=uuid.uuid4().hex,
            request=request,
            completion=asyncio.get_running_loop().create_future(),
            cancelled=asyncio.Event(),
            started_at=time.monotonic(),
        )
        self._emit(job, "queued")
        asyncio.create_task(self._run(job))
        return SingingHandle(job)

    async def cover(self, request: CoverRequest) -> SingingResult:
        return await (await self.submit(request)).wait()

    async def _run(self, job: _CoverJob) -> None:
        try:
            asset = self._assets.resolve(job.request.asset_id)
            self._emit(job, "asset_ready")
            if self._cancelled(job):
                return
            self._emit(job, "separation_started")
            try:
                separated = await self._separator.separate(asset, job.cancelled)
            except SingingError:
                raise
            except Exception:
                self._complete_failure(job, "separation_failed")
                return
            if self._cancelled(job):
                return
            self._emit(job, "separation_finished")
            self._emit(job, "conversion_started")
            try:
                vocal = await self._engine.convert(
                    separated.vocal,
                    job.request.singing_profile,
                    ConversionOptions(
                        transpose_semitones=job.request.transpose_semitones,
                        preserve_pitch=job.request.preserve_pitch,
                        preserve_expression=job.request.preserve_expression,
                        expression_preservation=job.request.expression_preservation,
                    ),
                    job.cancelled,
                )
            except SingingError:
                raise
            except Exception:
                self._complete_failure(job, "conversion_failed")
                return
            if self._cancelled(job):
                return
            self._emit(job, "conversion_finished")
            if job.request.output == "mixed":
                self._emit(job, "remix_started")
                try:
                    vocal = await self._remixer.mix(
                        vocal, separated.instrumental, job.request, job.cancelled
                    )
                except SingingError:
                    raise
                except Exception:
                    self._complete_failure(job, "remix_failed")
                    return
                if self._cancelled(job):
                    return
            self._complete_success(job, vocal)
        except SingingError as error:
            self._complete_failure(job, error.code)
        except asyncio.CancelledError:
            self._complete_failure(job, "cancelled")
        except Exception:
            self._complete_failure(job, "conversion_failed")

    def _cancelled(self, job: _CoverJob) -> bool:
        if job.cancelled.is_set():
            self._complete_failure(job, "cancelled")
            return True
        return False

    def _complete_success(self, job: _CoverJob, vocal: VocalArtifact) -> None:
        if job.completion.done():
            return
        self._emit(job, "cover_ready")
        job.completion.set_result(
            SingingResult(
                job_id=job.job_id,
                success=True,
                output=job.request.output,
                audio=vocal.audio,
                media_type="audio/wav",
                duration_ms=vocal.duration_ms,
                sample_rate=vocal.sample_rate,
                latency_ms=self._latency(job),
            )
        )

    def _complete_failure(self, job: _CoverJob, code: SingingErrorCode) -> None:
        if job.completion.done():
            return
        self._emit(job, "cancelled" if code == "cancelled" else "failed", code)
        job.completion.set_result(
            SingingResult(
                job_id=job.job_id,
                success=False,
                output=job.request.output,
                error_code=code,
                latency_ms=self._latency(job),
            )
        )

    def _emit(
        self,
        job: _CoverJob,
        kind: str,
        error_code: Optional[SingingErrorCode] = None,
    ) -> None:
        self._events.append(
            SingingEvent(
                job_id=job.job_id,
                kind=kind,
                timestamp_ms=round(time.monotonic() * 1000),
                error_code=error_code,
            )
        )
        del self._events[:-64]

    @staticmethod
    def _latency(job: _CoverJob) -> int:
        return round((time.monotonic() - job.started_at) * 1000)
