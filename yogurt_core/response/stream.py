from typing import Iterable, Tuple

from yogurt_contracts.response.events import (
    ResponseAbortedEvent, ResponseEvent, ResponseFinalizedEvent, ResponseStartedEvent, SegmentCommittedEvent,
)


RESPONSE_EVENT_TYPES = (ResponseStartedEvent, SegmentCommittedEvent, ResponseFinalizedEvent, ResponseAbortedEvent)


class ResponseEventStream:
    """Narrow response-only stream validator; it is intentionally not a global bus."""

    @staticmethod
    def validate(events: Iterable[object]) -> Tuple[ResponseEvent, ...]:
        accepted = []
        for event in events:
            if not isinstance(event, RESPONSE_EVENT_TYPES):
                raise TypeError("ResponseEventStream accepts response events only")
            accepted.append(event)
        return tuple(accepted)
