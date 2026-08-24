from __future__ import annotations

import re
import asyncio
import weakref
from typing import Callable
from typing import Mapping

from capabilities.mouth.playback import PlaybackSink


_SINK_ID = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")


class SerializedPlaybackSink:
    """Serializes playback per trusted sink without binding locks at import time."""

    def __init__(self, sink: PlaybackSink) -> None:
        self._sink = sink
        self._locks: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Lock]" = (
            weakref.WeakKeyDictionary()
        )

    def _lock(self) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        lock = self._locks.get(loop)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[loop] = lock
        return lock

    async def play(
        self,
        artifact,
        cancelled: asyncio.Event,
        mark_started: Callable[[], None],
    ) -> None:
        if cancelled.is_set():
            return
        lock = self._lock()
        acquire_task = asyncio.create_task(lock.acquire())
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
                lock.release()
            return
        try:
            await self._sink.play(artifact, cancelled, mark_started)
        finally:
            lock.release()


class SinkRegistry:
    """Trusted in-process sink registration; never resolves URLs or devices."""

    def __init__(self, sinks: Mapping[str, PlaybackSink] | None = None) -> None:
        self._sinks: dict[str, PlaybackSink] = {}
        for sink_id, sink in (sinks or {}).items():
            self.register(sink_id, sink)

    def register(self, sink_id: str, sink: PlaybackSink) -> None:
        if not _SINK_ID.fullmatch(sink_id) or not hasattr(sink, "play"):
            raise ValueError("invalid_sink")
        self._sinks[sink_id] = SerializedPlaybackSink(sink)

    def get(self, sink_id: str) -> PlaybackSink:
        try:
            return self._sinks[sink_id]
        except KeyError as error:
            raise ValueError("unknown_sink") from error

    def ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._sinks))


class OutputRouter:
    def __init__(self, registry: SinkRegistry, default_sink_id: str) -> None:
        registry.get(default_sink_id)
        self._registry = registry
        self.default_sink_id = default_sink_id

    def resolve(self, sink_id: str | None = None) -> tuple[str, PlaybackSink]:
        resolved_id = sink_id or self.default_sink_id
        return resolved_id, self._registry.get(resolved_id)
