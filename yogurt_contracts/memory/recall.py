"""Provider-neutral R3-A Memory recall read contracts.

These contracts carry untrusted semantic data only.  They never describe prompt
roles, tool calls, or provider transport details.
"""

from __future__ import annotations

import unicodedata
import hashlib
from enum import Enum
from typing import Optional, Tuple

from pydantic import field_validator, model_validator

from ..conversation.refs import ConversationRef
from ..foundation.identifiers import (
    BindingId,
    CapabilityId,
    MemoryConversationScopeId,
    MemoryObservationId,
    MemoryRecallQueryId,
    RuntimeInstanceId,
    TurnId,
)
from ..foundation.idempotency import RequestFingerprint
from ..foundation.model import FrozenContract
from ..foundation.time import Deadline, UtcInstant


MEMORY_RECALL_CAPABILITY_ID = "memory.recall"
_MAX_TEXT_LENGTH = 8192
_MAX_METADATA_ITEMS = 16
_MAX_CANDIDATES = 32
_MAX_TOKEN_BUDGET = 16384


def _bounded_text(value: str, field_name: str, maximum: int = 256) -> str:
    if not value or len(value) > maximum:
        raise ValueError(field_name + " must be non-empty and bounded")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError(field_name + " must not contain control characters")
    return value


def normalize_memory_text(value: str) -> str:
    """A deterministic, data-only comparison form; it is not a prompt transform."""

    return unicodedata.normalize("NFC", value.replace("\r\n", "\n").replace("\r", "\n")).strip()


class MemoryConversationScopeRef(FrozenContract):
    """Logical scope shared by every surface for one Core conversation identity."""

    scope_id: MemoryConversationScopeId
    persona_id: str
    user_id: str

    @field_validator("persona_id", "user_id")
    @classmethod
    def has_bounded_identity(cls, value: str) -> str:
        return _bounded_text(value, "memory scope identity")


def memory_conversation_scope_ref(conversation_ref: ConversationRef) -> MemoryConversationScopeRef:
    """Derive a stable logical scope without consulting the ingress surface."""

    material = "\x1f".join((
        conversation_ref.persona_id.value,
        conversation_ref.user_id.value,
        conversation_ref.conversation_id.value,
    ))
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()
    return MemoryConversationScopeRef(
        scope_id=MemoryConversationScopeId(value="memory-scope-" + digest),
        persona_id=conversation_ref.persona_id.value,
        user_id=conversation_ref.user_id.value,
    )


class MemoryRecallBindingSnapshot(FrozenContract):
    binding_id: BindingId
    logical_provider_id: str
    runtime_instance_id: RuntimeInstanceId
    runtime_generation: int
    binding_revision: str
    memory_domain_id: str
    capability_id: CapabilityId

    @field_validator("logical_provider_id", "binding_revision", "memory_domain_id")
    @classmethod
    def has_bounded_reference(cls, value: str) -> str:
        return _bounded_text(value, "memory binding reference")

    @field_validator("runtime_generation")
    @classmethod
    def has_positive_generation(cls, value: int) -> int:
        if value < 1:
            raise ValueError("runtime_generation must be at least 1")
        return value

    @model_validator(mode="after")
    def is_memory_recall_binding(self) -> "MemoryRecallBindingSnapshot":
        if self.capability_id.value != MEMORY_RECALL_CAPABILITY_ID:
            raise ValueError("memory recall binding capability_id must be memory.recall")
        return self


class MemoryRecallCapabilityState(str, Enum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"


class RecallEligibility(str, Enum):
    ELIGIBLE = "eligible"
    OBSERVATION_NOT_CONFIRMED = "observation_not_confirmed"
    PRIOR_CAUSAL_OBSERVATION_PENDING = "prior_causal_observation_pending"
    HISTORY_INCOMPLETE = "history_incomplete"
    DEADLINE_EXHAUSTED = "deadline_exhausted"
    UNAUTHORIZED = "unauthorized"
    CAPABILITY_UNAVAILABLE = "capability_unavailable"
    STALE_BINDING = "stale_binding"
    INVALID_SCOPE = "invalid_scope"


class MemoryRecallCallOutcome(str, Enum):
    SUCCESS = "success"
    UNAVAILABLE = "unavailable"
    DEADLINE_EXCEEDED = "deadline_exceeded"
    INVALID_PROVIDER_RESPONSE = "invalid_provider_response"


class MemoryRecallDecision(str, Enum):
    AVAILABLE = "available"
    EMPTY = "empty"
    SKIPPED = "skipped"
    BLOCKED = "blocked"


class MemoryRecallQuery(FrozenContract):
    recall_query_id: MemoryRecallQueryId
    conversation_ref: ConversationRef
    turn_id: TurnId
    memory_conversation_scope_ref: MemoryConversationScopeRef
    normalized_user_message: str
    current_input_fingerprint: RequestFingerprint
    current_observation_id: Optional[MemoryObservationId] = None
    max_items: int
    token_budget: int
    deadline: Deadline
    query_fingerprint: RequestFingerprint

    @field_validator("normalized_user_message")
    @classmethod
    def has_normalized_message(cls, value: str) -> str:
        if value != normalize_memory_text(value):
            raise ValueError("normalized_user_message must use the memory normalization form")
        return _bounded_text(value, "normalized_user_message", _MAX_TEXT_LENGTH)

    @field_validator("max_items")
    @classmethod
    def has_bounded_item_count(cls, value: int) -> int:
        if value < 1 or value > _MAX_CANDIDATES:
            raise ValueError("max_items must be between 1 and " + str(_MAX_CANDIDATES))
        return value

    @field_validator("token_budget")
    @classmethod
    def has_bounded_token_budget(cls, value: int) -> int:
        if value < 1 or value > _MAX_TOKEN_BUDGET:
            raise ValueError("token_budget must be between 1 and " + str(_MAX_TOKEN_BUDGET))
        return value

    @model_validator(mode="after")
    def has_matching_logical_scope(self) -> "MemoryRecallQuery":
        if self.memory_conversation_scope_ref.persona_id != self.conversation_ref.persona_id.value:
            raise ValueError("memory conversation scope persona must match query conversation")
        if self.memory_conversation_scope_ref.user_id != self.conversation_ref.user_id.value:
            raise ValueError("memory conversation scope user must match query conversation")
        return self


class MemoryRecallCandidateEvidence(FrozenContract):
    evidence_type: str
    reference: str
    value: str

    @field_validator("evidence_type", "reference", "value")
    @classmethod
    def has_bounded_value(cls, value: str) -> str:
        return _bounded_text(value, "candidate evidence", 512)


class MemoryProviderMetadata(FrozenContract):
    key: str
    value: str

    @field_validator("key", "value")
    @classmethod
    def has_bounded_value(cls, value: str) -> str:
        return _bounded_text(value, "provider metadata", 512)


class MemoryRecallCandidate(FrozenContract):
    """Normalized semantic data; consumers must never treat it as instructions."""

    stable_candidate_id: str
    kind: str
    normalized_text: str
    source_ref: str
    source_turn_id: Optional[TurnId] = None
    source_observation_id: Optional[MemoryObservationId] = None
    content_fingerprint: RequestFingerprint
    advisory_score: float
    content_size: int
    bounded_evidence: Tuple[MemoryRecallCandidateEvidence, ...] = ()

    @field_validator("stable_candidate_id", "kind", "source_ref")
    @classmethod
    def has_bounded_reference(cls, value: str) -> str:
        return _bounded_text(value, "candidate reference")

    @field_validator("normalized_text")
    @classmethod
    def has_normalized_text(cls, value: str) -> str:
        if value != normalize_memory_text(value):
            raise ValueError("candidate normalized_text must use the memory normalization form")
        return _bounded_text(value, "candidate normalized_text", _MAX_TEXT_LENGTH)

    @field_validator("content_size")
    @classmethod
    def has_actual_content_size(cls, value: int) -> int:
        if value < 1 or value > _MAX_TEXT_LENGTH:
            raise ValueError("candidate content_size must be bounded")
        return value

    @field_validator("bounded_evidence")
    @classmethod
    def has_bounded_evidence(cls, value: Tuple[MemoryRecallCandidateEvidence, ...]) -> Tuple[MemoryRecallCandidateEvidence, ...]:
        if len(value) > _MAX_METADATA_ITEMS:
            raise ValueError("candidate evidence is too large")
        return value

    @model_validator(mode="after")
    def content_size_matches_normalized_text(self) -> "MemoryRecallCandidate":
        if self.content_size != len(self.normalized_text):
            raise ValueError("candidate content_size must equal normalized_text length")
        return self


class MemoryRecallEvidence(FrozenContract):
    recall_query_id: MemoryRecallQueryId
    conversation_ref: ConversationRef
    turn_id: TurnId
    query_fingerprint: RequestFingerprint
    memory_conversation_scope_ref: MemoryConversationScopeRef
    logical_provider_id: str
    memory_domain_id: str
    provider_binding_ref: MemoryRecallBindingSnapshot
    produced_by_generation: int
    retrieved_at: UtcInstant
    decision: MemoryRecallDecision
    candidates: Tuple[MemoryRecallCandidate, ...]
    advisory_token_estimate: int
    bounded_provider_metadata: Tuple[MemoryProviderMetadata, ...] = ()

    @field_validator("logical_provider_id", "memory_domain_id")
    @classmethod
    def has_bounded_provider_reference(cls, value: str) -> str:
        return _bounded_text(value, "provider reference")

    @field_validator("produced_by_generation", "advisory_token_estimate")
    @classmethod
    def has_non_negative_number(cls, value: int) -> int:
        if value < 0:
            raise ValueError("generation and token estimate must be non-negative")
        return value

    @field_validator("candidates")
    @classmethod
    def has_bounded_candidates(cls, value: Tuple[MemoryRecallCandidate, ...]) -> Tuple[MemoryRecallCandidate, ...]:
        if len(value) > _MAX_CANDIDATES:
            raise ValueError("too many recall candidates")
        return value

    @field_validator("bounded_provider_metadata")
    @classmethod
    def has_bounded_metadata(cls, value: Tuple[MemoryProviderMetadata, ...]) -> Tuple[MemoryProviderMetadata, ...]:
        if len(value) > _MAX_METADATA_ITEMS:
            raise ValueError("provider metadata is too large")
        return value

    @model_validator(mode="after")
    def has_consistent_provenance(self) -> "MemoryRecallEvidence":
        if self.logical_provider_id != self.provider_binding_ref.logical_provider_id:
            raise ValueError("evidence provider must match binding")
        if self.memory_domain_id != self.provider_binding_ref.memory_domain_id:
            raise ValueError("evidence memory domain must match binding")
        if self.produced_by_generation != self.provider_binding_ref.runtime_generation:
            raise ValueError("evidence generation must match producing binding")
        if self.decision == MemoryRecallDecision.AVAILABLE and not self.candidates:
            raise ValueError("available recall evidence requires candidates")
        if self.decision != MemoryRecallDecision.AVAILABLE and self.candidates:
            raise ValueError("non-available recall evidence must not carry candidates")
        return self


class MemoryRecallProviderResult(FrozenContract):
    call_outcome: MemoryRecallCallOutcome
    evidence: Optional[MemoryRecallEvidence] = None

    @model_validator(mode="after")
    def has_outcome_consistent_evidence(self) -> "MemoryRecallProviderResult":
        if self.call_outcome == MemoryRecallCallOutcome.SUCCESS and self.evidence is None:
            raise ValueError("successful recall must include evidence")
        if self.call_outcome != MemoryRecallCallOutcome.SUCCESS and self.evidence is not None:
            raise ValueError("failed recall must not include evidence")
        return self


class MemoryRecallDispatchResult(FrozenContract):
    """Core's separated eligibility, provider outcome, and semantic evidence result."""

    capability_state: MemoryRecallCapabilityState
    eligibility: RecallEligibility
    call_outcome: Optional[MemoryRecallCallOutcome] = None
    evidence: Optional[MemoryRecallEvidence] = None

    @model_validator(mode="after")
    def preserves_the_three_recall_layers(self) -> "MemoryRecallDispatchResult":
        if self.eligibility != RecallEligibility.ELIGIBLE:
            if self.call_outcome is not None or self.evidence is not None:
                raise ValueError("ineligible recall must not have a provider outcome or evidence")
            return self
        if self.call_outcome is None:
            raise ValueError("eligible recall must have a provider outcome")
        if self.call_outcome == MemoryRecallCallOutcome.SUCCESS and self.evidence is None:
            raise ValueError("successful recall dispatch must include evidence")
        if self.call_outcome != MemoryRecallCallOutcome.SUCCESS and self.evidence is not None:
            raise ValueError("failed recall dispatch must not include evidence")
        return self
