"""Stable operation-id deduplication semantics for public Memory observations."""

from enum import Enum
from typing import Union

from .observations import CommittedContentObservation, PresentationObservation, UserContentObservation


MemoryObservation = Union[UserContentObservation, CommittedContentObservation, PresentationObservation]


class MemoryObservationDedupDecision(str, Enum):
    DUPLICATE_RETRY = "duplicate_retry"
    PAYLOAD_CONFLICT = "payload_conflict"
    DIFFERENT_OBSERVATION = "different_observation"


def classify_memory_observation_retry(existing: MemoryObservation, retry: MemoryObservation) -> MemoryObservationDedupDecision:
    if existing.operation_id != retry.operation_id:
        return MemoryObservationDedupDecision.DIFFERENT_OBSERVATION
    return MemoryObservationDedupDecision.DUPLICATE_RETRY if existing == retry else MemoryObservationDedupDecision.PAYLOAD_CONFLICT
