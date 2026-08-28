"""Small stateful behavioral fakes for proving the profiles detect defects."""

from datetime import datetime, timezone
from typing import Dict, Optional, Tuple

from yogurt_contracts.capability.framework import BaseCapabilityDescriptor, is_contract_compatible
from yogurt_contracts.capability.offer import CapabilityOffer
from yogurt_contracts.conversation.fingerprint import TurnFingerprint
from yogurt_contracts.foundation.identifiers import (
    BindingId, CapabilityId, GenerationAuthorityId, NodeId, OperationAttemptId,
    OperationId, ReceiptId, ResponseId, RuntimeInstanceId, RuntimeSlotId, SegmentId, TurnId,
)
from yogurt_contracts.foundation.time import UtcInstant
from yogurt_contracts.foundation.versioning import ContractVersion
from yogurt_contracts.memory.deduplication import MemoryObservationDedupDecision, classify_memory_observation_retry
from yogurt_contracts.memory.observations import CommittedContentObservation, PresentationObservation
from yogurt_contracts.memory.projection import project_memory_exposure
from yogurt_contracts.memory.projection import MemoryExposureProjection
from yogurt_contracts.presentation.extent import TextPresentationExtent, VoicePresentationExtent
from yogurt_contracts.presentation.facts import PresentationTerminalOutcome
from yogurt_contracts.presentation.projection import PresentationProjection
from yogurt_contracts.runtime.generation import GenerationAssignment
from yogurt_contracts.runtime.identity import RuntimeIdentity
from yogurt_conformance.protocols.adapters import CoreTurnAdmission, OperationDispatchState, PlaybackState


NOW = UtcInstant(value=datetime(2026, 1, 1, tzinfo=timezone.utc))


class GoodCorePublicFake:
    def __init__(self) -> None:
        self._turns: Dict[TurnId, Tuple[TurnFingerprint, str, Optional[ResponseId], bool, bool]] = {}
        self._next = 0

    def submit_turn(self, turn_id: TurnId, fingerprint: TurnFingerprint) -> CoreTurnAdmission:
        record = self._turns.get(turn_id)
        if record is not None:
            return CoreTurnAdmission(record[1], conflict=record[0] != fingerprint)
        self._next += 1
        logical = "turn-" + str(self._next)
        self._turns[turn_id] = (fingerprint, logical, None, False, False)
        return CoreTurnAdmission(logical)

    def start_response(self, logical_turn: str) -> Optional[ResponseId]:
        for turn_id, record in self._turns.items():
            if record[1] == logical_turn:
                if record[2] is not None or record[4]: return None
                response = ResponseId(value="response-" + logical_turn)
                self._turns[turn_id] = (record[0], record[1], response, record[3], record[4])
                return response
        return None

    def append_segment(self, logical_turn: str, response_id: ResponseId, segment_id: SegmentId, text: str) -> bool:
        del segment_id, text
        return any(record[1] == logical_turn and record[2] == response_id and not record[3] and not record[4] for record in self._turns.values())

    def finalize_response(self, logical_turn: str, response_id: ResponseId) -> bool:
        for turn_id, record in self._turns.items():
            if record[1] == logical_turn and record[2] == response_id and not record[3]:
                self._turns[turn_id] = (record[0], record[1], record[2], True, record[4]); return True
        return False

    def complete_turn(self, logical_turn: str) -> bool:
        for turn_id, record in self._turns.items():
            if record[1] == logical_turn and record[3] and not record[4]:
                self._turns[turn_id] = (record[0], record[1], record[2], record[3], True); return True
        return False

    def record_late_downstream(self, logical_turn: str) -> bool:
        return any(record[1] == logical_turn and record[4] for record in self._turns.values())

    def fallback_attempt(self, logical_turn: str, response_id: ResponseId) -> Optional[str]:
        return "attempt-fallback" if any(record[1] == logical_turn and record[2] == response_id for record in self._turns.values()) else None


class DuplicateTurnExecutionFake(GoodCorePublicFake):
    def submit_turn(self, turn_id: TurnId, fingerprint: TurnFingerprint) -> CoreTurnAdmission:
        admission = super().submit_turn(turn_id, fingerprint)
        if not admission.conflict:
            self._next += 1
            return CoreTurnAdmission("duplicate-" + str(self._next))
        return admission


class GoodMemoryFake:
    def __init__(self) -> None: self._observations: Dict[OperationId, object] = {}
    def store_observation(self, observation: object) -> MemoryObservationDedupDecision:
        operation_id = getattr(observation, "operation_id")
        existing = self._observations.get(operation_id)
        if existing is None: self._observations[operation_id] = observation; return MemoryObservationDedupDecision.DIFFERENT_OBSERVATION
        return classify_memory_observation_retry(existing, observation)  # type: ignore[arg-type]
    def project_segment(self, committed: CommittedContentObservation, observations: Tuple[PresentationObservation, ...]) -> MemoryExposureProjection:
        return project_memory_exposure(committed, observations)


class CommitMeansSeenMemoryFake(GoodMemoryFake):
    def project_segment(self, committed: CommittedContentObservation, observations: Tuple[PresentationObservation, ...]) -> MemoryExposureProjection:
        projection = super().project_segment(committed, observations)
        return projection.model_copy(update={"is_fully_exposed": True})


class RegressingExposureFake(GoodMemoryFake):
    def project_segment(self, committed: CommittedContentObservation, observations: Tuple[PresentationObservation, ...]) -> MemoryExposureProjection:
        projection = super().project_segment(committed, observations)
        if projection.text_extent is not None:
            return projection.model_copy(update={"text_extent": TextPresentationExtent(segment_id=committed.segment_id, end_text_offset=0)})
        return projection


class GoodSurfaceFake:
    def __init__(self) -> None: self._accepted: list[str] = []; self._delivered: list[str] = []; self._text = 0; self._read: list[str] = []; self._terminal: Optional[PresentationTerminalOutcome] = None
    def accept(self, receipt: str) -> None: self._accepted.append(receipt)
    def deliver(self, receipt: str) -> None:
        if receipt not in self._accepted: raise ValueError("delivery requires acceptance")
        self._delivered.append(receipt)
    def report_text(self, receipt: str, offset: int) -> None:
        if receipt not in self._delivered: raise ValueError("progress requires delivery")
        self._text = max(self._text, offset)
    def report_read(self, receipt: str) -> None:
        if receipt not in self._delivered: raise ValueError("read requires delivery")
        self._read.append(receipt)
    def terminal(self, receipt: str, outcome: str) -> None:
        del receipt
        value = PresentationTerminalOutcome(outcome)
        if self._terminal is not None and self._terminal != value: raise ValueError("conflicting terminal")
        self._terminal = value
    def is_read(self, receipt: str) -> bool: return receipt in self._read
    def projection(self) -> PresentationProjection:
        return PresentationProjection(operation_id=OperationId(value="surface-operation"), accepted_receipts=tuple(ReceiptId(value=value) for value in self._accepted), delivered_receipts=tuple(ReceiptId(value=value) for value in self._delivered), max_text_extent=TextPresentationExtent(segment_id=SegmentId(value="surface-segment"), end_text_offset=self._text) if self._text else None, terminal_outcome=self._terminal)


class ForgedReadReceiptSurface(GoodSurfaceFake):
    def is_read(self, receipt: str) -> bool: return True


class GoodVoiceFake:
    def __init__(self) -> None: self._states: Dict[SegmentId, PlaybackState] = {}
    def submit_segment(self, segment_id: SegmentId, committed: bool) -> PlaybackState:
        state = PlaybackState(intent_count=1 if committed else 0); self._states[segment_id] = state; return state
    def retry_playback(self, segment_id: SegmentId) -> PlaybackState: return self._states[segment_id]
    def report_progress(self, segment_id: SegmentId, duration_ms: int, end_text_offset: Optional[int]) -> PlaybackState:
        old = self._states[segment_id]; prior = old.extent
        extent = VoicePresentationExtent(segment_id=segment_id, played_duration_ms=max(duration_ms, prior.played_duration_ms if prior else 0), end_text_offset=max((end_text_offset or 0), (prior.end_text_offset or 0) if prior else 0))
        state = PlaybackState(old.intent_count, old.completed, old.cancelled, old.interrupted, extent); self._states[segment_id] = state; return state
    def interrupt(self, segment_id: SegmentId) -> PlaybackState:
        old = self._states[segment_id]; state = PlaybackState(old.intent_count, False, old.cancelled, True, old.extent); self._states[segment_id] = state; return state
    def cancel(self, segment_id: SegmentId) -> PlaybackState:
        old = self._states[segment_id]; state = PlaybackState(old.intent_count, False, True, old.interrupted, old.extent); self._states[segment_id] = state; return state


class DraftSpeakingVoiceFake(GoodVoiceFake):
    def submit_segment(self, segment_id: SegmentId, committed: bool) -> PlaybackState: return super().submit_segment(segment_id, True)


class GoodOperationProviderFake:
    def __init__(self) -> None: self._states: Dict[OperationId, Tuple[str, OperationDispatchState, bool]] = {}
    def commit_intent(self, operation_id: OperationId, fingerprint: str, dispatchable: bool = True) -> OperationDispatchState:
        old = self._states.get(operation_id)
        if old is not None:
            if old[0] != fingerprint: raise ValueError("operation fingerprint conflict")
            return old[1]
        state = OperationDispatchState(operation_id); self._states[operation_id] = (fingerprint, state, dispatchable); return state
    def dispatch(self, operation_id: OperationId, authorization_valid: bool = True, generation_current: bool = True) -> OperationDispatchState:
        fingerprint, old, dispatchable = self._states[operation_id]
        if not dispatchable or not authorization_valid or not generation_current: return old
        attempt = OperationAttemptId(value="attempt-" + str(len(old.attempts) + 1))
        state = OperationDispatchState(operation_id, old.attempts + (attempt,), max(old.side_effect_count, 1), old.outcome_unknown); self._states[operation_id] = (fingerprint, state, dispatchable); return state
    def unknown_outcome(self, operation_id: OperationId) -> OperationDispatchState:
        fingerprint, old, dispatchable = self._states[operation_id]; state = OperationDispatchState(operation_id, old.attempts, old.side_effect_count, True); self._states[operation_id] = (fingerprint, state, dispatchable); return state


class DispatchWithoutIntentFake(GoodOperationProviderFake):
    def dispatch(self, operation_id: OperationId, authorization_valid: bool = True, generation_current: bool = True) -> OperationDispatchState:
        if operation_id not in self._states: self._states[operation_id] = ("forged", OperationDispatchState(operation_id), True)
        return super().dispatch(operation_id, authorization_valid, generation_current)


class GoodCapabilityProviderFake:
    def __init__(self) -> None: self._offer: Optional[CapabilityOffer[BaseCapabilityDescriptor]] = None
    def publish_offer(self, offer: CapabilityOffer[BaseCapabilityDescriptor]) -> None: self._offer = offer
    def bind(self, required_major: int, required_minor: int, generation_current: bool = True, binding_expired: bool = False) -> bool:
        if self._offer is None or not generation_current or binding_expired: return False
        return is_contract_compatible(ContractVersion(major=required_major, minor=required_minor), self._offer.contract_version)


class GoodRuntimeLifecycleFake:
    def __init__(self) -> None: self._assignments: Dict[str, GenerationAssignment] = {}
    def start(self, slot: str, instance: str) -> RuntimeIdentity:
        prior = self._assignments.get(slot); generation = (prior.generation if prior else 0) + 1
        assignment = GenerationAssignment(runtime_slot_id=RuntimeSlotId(value=slot), runtime_instance_id=RuntimeInstanceId(value=instance), generation=generation, issued_by=GenerationAuthorityId(value="authority"), issued_at=NOW, authority_revision="r1")
        self._assignments[slot] = assignment
        return RuntimeIdentity(runtime_type="test", runtime_slot_id=assignment.runtime_slot_id, runtime_instance_id=assignment.runtime_instance_id, runtime_generation=generation, node_id=NodeId(value="node"))
    def current_assignment(self, slot: str) -> Optional[GenerationAssignment]: return self._assignments.get(slot)
    def lease_expired(self, slot: str) -> bool: return slot not in self._assignments
