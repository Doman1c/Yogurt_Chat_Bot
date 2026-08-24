from __future__ import annotations

import re
from typing import Mapping

from capabilities.mouth.playback import PlaybackSink


_SINK_ID = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")


class SinkRegistry:
    """Trusted in-process sink registration; never resolves URLs or devices."""

    def __init__(self, sinks: Mapping[str, PlaybackSink] | None = None) -> None:
        self._sinks: dict[str, PlaybackSink] = {}
        for sink_id, sink in (sinks or {}).items():
            self.register(sink_id, sink)

    def register(self, sink_id: str, sink: PlaybackSink) -> None:
        if not _SINK_ID.fullmatch(sink_id) or not hasattr(sink, "play"):
            raise ValueError("invalid_sink")
        self._sinks[sink_id] = sink

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
