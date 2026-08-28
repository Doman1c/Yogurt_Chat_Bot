"""Test-only generation authorities for exercising durable monotonic issuance contracts."""

from datetime import datetime, timezone
from typing import Dict, Optional, Protocol, Tuple

from yogurt_contracts.foundation.identifiers import GenerationAuthorityId, RuntimeInstanceId, RuntimeSlotId
from yogurt_contracts.foundation.time import UtcInstant
from yogurt_contracts.runtime.generation import GenerationAssignment


GenerationAuthorityHarnessState = Tuple[Tuple[Tuple[RuntimeSlotId, int], ...], Tuple[GenerationAssignment, ...]]


class GenerationAuthorityHarness(Protocol):
    """Test-only extension for verifying durable issuance and current-assignment recovery."""

    def issue(self, runtime_slot_id: RuntimeSlotId, runtime_instance_id: RuntimeInstanceId) -> GenerationAssignment:
        ...

    def current_assignment(self, runtime_slot_id: RuntimeSlotId) -> Optional[GenerationAssignment]:
        ...

    def snapshot_state(self) -> GenerationAuthorityHarnessState:
        ...

    def restore_state(self, state: GenerationAuthorityHarnessState) -> None:
        ...


class FakeGenerationAuthority:
    def __init__(self, issued_by: GenerationAuthorityId, authority_revision: str = "test-r1") -> None:
        self._issued_by = issued_by
        self._authority_revision = authority_revision
        self._last_generation: Dict[RuntimeSlotId, int] = {}
        self._current_assignment: Dict[RuntimeSlotId, GenerationAssignment] = {}

    def issue(self, runtime_slot_id: RuntimeSlotId, runtime_instance_id: RuntimeInstanceId) -> GenerationAssignment:
        generation = self._last_generation.get(runtime_slot_id, 0) + 1
        self._last_generation[runtime_slot_id] = generation
        assignment = GenerationAssignment(
            runtime_slot_id=runtime_slot_id,
            runtime_instance_id=runtime_instance_id,
            generation=generation,
            issued_by=self._issued_by,
            issued_at=UtcInstant(value=datetime(2026, 1, 1, tzinfo=timezone.utc)),
            authority_revision=self._authority_revision,
        )
        self._current_assignment[runtime_slot_id] = assignment
        return assignment

    def current_assignment(self, runtime_slot_id: RuntimeSlotId) -> Optional[GenerationAssignment]:
        return self._current_assignment.get(runtime_slot_id)

    def snapshot_state(self) -> GenerationAuthorityHarnessState:
        counters = tuple(sorted(self._last_generation.items(), key=lambda item: item[0].value))
        assignments = tuple(sorted(self._current_assignment.values(), key=lambda item: item.runtime_slot_id.value))
        return counters, assignments

    def restore_state(self, state: GenerationAuthorityHarnessState) -> None:
        counters, assignments = state
        self._last_generation = dict(counters)
        self._current_assignment = {assignment.runtime_slot_id: assignment for assignment in assignments}


class ResettingGenerationAuthorityFake(FakeGenerationAuthority):
    """Deliberately invalid authority: it discards durable generation state on restart."""

    def restore_state(self, state: GenerationAuthorityHarnessState) -> None:
        del state
        self._last_generation = {}
        self._current_assignment = {}
