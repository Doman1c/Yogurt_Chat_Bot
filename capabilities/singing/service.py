from __future__ import annotations

import asyncio
import time
import uuid
import weakref
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
    capacity: "_JobCapacity | None" = None
    task: "asyncio.Task[None] | None" = None


class _JobCapacity:
    """A bounded per-event-loop cover-job gate with cancellation-aware waiting."""

    def __init__(self, max_concurrent_jobs: int, max_pending_jobs: int) -> None:
        self._semaphore = asyncio.Semaphore(max_concurrent_jobs)
        self._max_outstanding = max_concurrent_jobs + max_pending_jobs
        self._outstanding = 0
        self._lock = asyncio.Lock()

    async def reserve(self) -> bool:
        async with self._lock:
            if self._outstanding >= self._max_outstanding:
                return False
            self._outstanding += 1
            return True

    async def release_reservation(self) -> None:
        async with self._lock:
            assert self._outstanding > 0
            self._outstanding -= 1

    async def acquire_or_cancel(self, cancelled: asyncio.Event) -> bool:
        if cancelled.is_set():
            return False
        acquire_task = asyncio.create_task(self._semaphore.acquire())
        cancel_task = asyncio.create_task(cancelled.wait())
        done, pending = await asyncio.wait(
            {acquire_task, cancel_task}, return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        acquired = acquire_task in done and acquire_task.result() is True
        if not acquired or cancelled.is_set():
            if acquired:
                self._semaphore.release()
            return False
        return True

    def release_slot(self) -> None:
        self._semaphore.release()


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
        *,
        max_concurrent_jobs: int = 1,
        max_pending_jobs: int = 4,
    ) -> None:
        if not (1 <= max_concurrent_jobs <= 4):
            raise ValueError("invalid_max_concurrent_jobs")
        if not (1 <= max_pending_jobs <= 8):
            raise ValueError("invalid_max_pending_jobs")
        self._assets = assets
        self._separator = separator
        self._engine = engine
        self._remixer = remixer
        self._max_concurrent_jobs = max_concurrent_jobs
        self._max_pending_jobs = max_pending_jobs
        self._capacities: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, _JobCapacity]" = (
            weakref.WeakKeyDictionary()
        )
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
        handle = SingingHandle(job)
        if (
            request.output == "mixed"
            and request.transpose_semitones != 0
            and not self._remixer.supports_synchronized_transpose
        ):
            self._complete_failure(job, "unsupported_transpose")
            return handle
        capacity = self._capacity()
        if not await capacity.reserve():
            self._complete_failure(job, "queue_full")
            return handle
        job.capacity = capacity
        self._emit(job, "queued")
        job.task = asyncio.create_task(self._run(job))
        return handle

    async def cover(self, request: CoverRequest) -> SingingResult:
        return await (await self.submit(request)).wait()

    async def _run(self, job: _CoverJob) -> None:
        acquired_slot = False
        try:
            assert job.capacity is not None
            if not await job.capacity.acquire_or_cancel(job.cancelled):
                self._complete_failure(job, "cancelled")
                return
            acquired_slot = True
            if self._cancelled(job):
                return
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
        finally:
            if acquired_slot:
                assert job.capacity is not None
                job.capacity.release_slot()
            if job.capacity is not None:
                await job.capacity.release_reservation()

    def _capacity(self) -> _JobCapacity:
        loop = asyncio.get_running_loop()
        capacity = self._capacities.get(loop)
        if capacity is None:
            capacity = _JobCapacity(self._max_concurrent_jobs, self._max_pending_jobs)
            self._capacities[loop] = capacity
        return capacity

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
