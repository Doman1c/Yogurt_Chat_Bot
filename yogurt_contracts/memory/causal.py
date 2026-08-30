"""Provider-neutral R3-B semantic observation and causal-lane contracts."""

from __future__ import annotations

import hashlib
import json
from enum import Enum
from typing import Optional, Tuple, Union

from pydantic import field_validator, model_validator

from ..foundation.idempotency import RequestFingerprint
from ..foundation.identifiers import OperationId
from ..foundation.model import FrozenContract
from .observations import CommittedContentObservation, PresentationObservation, UserContentObservation
from .recall import MemoryConversationScopeRef, memory_conversation_scope_ref


MemoryObservation = Union[UserContentObservation, CommittedContentObservation, PresentationObservation]


class MemoryObservationParticipation(str, Enum):
    PARTICIPATES = "participates"
    DOES_NOT_PARTICIPATE = "does_not_participate"


class MemoryCausalResolution(str, Enum):
    PENDING = "pending"
    APPLIED = "applied"
    RESOLVED_NOT_APPLIED = "resolved_not_applied"
    UNRESOLVED = "unresolved"


def memory_observation_participation(observation: MemoryObservation) -> MemoryObservationParticipation:
    """Only user content belongs to the frozen legacy V5 causal lane."""

    if isinstance(observation, UserContentObservation):
        return MemoryObservationParticipation.PARTICIPATES
    return MemoryObservationParticipation.DOES_NOT_PARTICIPATE


def memory_observation_fingerprint(observation: UserContentObservation) -> RequestFingerprint:
    """Stable semantic fingerprint, independent of runtime, surface, and provider transport."""

    payload = {
        "v": 1,
        "conversation": observation.conversation_ref.conversation_id.value,
        "persona": observation.conversation_ref.persona_id.value,
        "user": observation.conversation_ref.user_id.value,
        "turn": observation.turn_ref.turn_id.value,
        "input": None if observation.input_id is None else observation.input_id.value,
        "content": observation.content,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return RequestFingerprint(value=hashlib.sha256(encoded).hexdigest())


class MemoryObservationOperation(FrozenContract):
    """The semantic observation correlated with one durable R2 operation intent."""

    operation_id: OperationId
    observation: UserContentObservation
    scope_ref: MemoryConversationScopeRef
    logical_provider_domain: str
    observation_fingerprint: RequestFingerprint

    @field_validator("logical_provider_domain")
    @classmethod
    def has_bounded_domain(cls, value: str) -> str:
        if not value or len(value) > 256:
            raise ValueError("logical_provider_domain must be non-empty and bounded")
        return value

    @model_validator(mode="after")
    def is_semantically_correlated(self) -> "MemoryObservationOperation":
        if self.observation.operation_id != self.operation_id:
            raise ValueError("memory observation operation_id must match observation")
        if self.scope_ref != memory_conversation_scope_ref(self.observation.conversation_ref):
            raise ValueError("memory observation scope must be derived from its conversation")
        if self.observation_fingerprint != memory_observation_fingerprint(self.observation):
            raise ValueError("memory observation fingerprint must match semantic observation")
        return self


class MemoryCausalOperation(FrozenContract):
    operation_id: OperationId
    sequence: int
    observation_fingerprint: RequestFingerprint
    resolution: MemoryCausalResolution = MemoryCausalResolution.PENDING

    @field_validator("sequence")
    @classmethod
    def has_positive_sequence(cls, value: int) -> int:
        if value < 1:
            raise ValueError("memory causal sequence must be positive")
        return value


class MemoryCausalState(FrozenContract):
    scope_ref: MemoryConversationScopeRef
    logical_provider_domain: str
    next_sequence: int = 1
    resolved_through_sequence: int = 0
    applied_through_sequence: int = 0
    first_unresolved_sequence: Optional[int] = None
    history_complete: bool = True
    revision: int = 1

    @field_validator("logical_provider_domain")
    @classmethod
    def has_bounded_domain(cls, value: str) -> str:
        if not value or len(value) > 256:
            raise ValueError("logical_provider_domain must be non-empty and bounded")
        return value

    @model_validator(mode="after")
    def has_consistent_watermarks(self) -> "MemoryCausalState":
        if self.next_sequence < 1 or self.resolved_through_sequence < 0 or self.applied_through_sequence < 0:
            raise ValueError("memory causal sequence values must be non-negative")
        if self.resolved_through_sequence >= self.next_sequence:
            raise ValueError("resolved sequence must precede next sequence")
        if self.applied_through_sequence > self.resolved_through_sequence:
            raise ValueError("applied sequence cannot exceed resolved sequence")
        if self.first_unresolved_sequence is not None and self.first_unresolved_sequence < 1:
            raise ValueError("first unresolved sequence must be positive")
        if self.revision < 1:
            raise ValueError("memory causal state revision must be positive")
        return self


class MemoryCausalLaneSnapshot(FrozenContract):
    state: MemoryCausalState
    operations: Tuple[MemoryCausalOperation, ...] = ()

    @model_validator(mode="after")
    def has_unique_ordered_operations(self) -> "MemoryCausalLaneSnapshot":
        sequences = tuple(operation.sequence for operation in self.operations)
        operation_ids = tuple(operation.operation_id for operation in self.operations)
        if len(set(sequences)) != len(sequences) or len(set(operation_ids)) != len(operation_ids):
            raise ValueError("memory causal operations must have unique operation IDs and sequences")
        if tuple(sorted(sequences)) != sequences:
            raise ValueError("memory causal operations must be sequence ordered")
        return self
