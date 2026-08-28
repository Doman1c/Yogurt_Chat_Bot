"""Test-only generation authorities for exercising durable monotonic issuance contracts."""

from datetime import datetime, timezone
from typing import Dict, Tuple

from yogurt_contracts.foundation.identifiers import GenerationAuthorityId, RuntimeInstanceId, RuntimeSlotId
from yogurt_contracts.foundation.time import UtcInstant
from yogurt_contracts.runtime.generation import GenerationAssignment


class FakeGenerationAuthority:
    def __init__(self, issued_by: GenerationAuthorityId, authority_revision: str = "test-r1") -> None:
        self._issued_by = issued_by
        self._authority_revision = authority_revision
        self._last_generation: Dict[RuntimeSlotId, int] = {}

    def issue(self, runtime_slot_id: RuntimeSlotId, runtime_instance_id: RuntimeInstanceId) -> GenerationAssignment:
        generation = self._last_generation.get(runtime_slot_id, 0) + 1
        self._last_generation[runtime_slot_id] = generation
        return GenerationAssignment(
            runtime_slot_id=runtime_slot_id,
            runtime_instance_id=runtime_instance_id,
            generation=generation,
            issued_by=self._issued_by,
            issued_at=UtcInstant(value=datetime(2026, 1, 1, tzinfo=timezone.utc)),
            authority_revision=self._authority_revision,
        )

    def snapshot_state(self) -> Tuple[Tuple[RuntimeSlotId, int], ...]:
        return tuple(sorted(self._last_generation.items(), key=lambda item: item[0].value))

    def restore_state(self, state: Tuple[Tuple[RuntimeSlotId, int], ...]) -> None:
        self._last_generation = dict(state)


class ResettingGenerationAuthorityFake(FakeGenerationAuthority):
    """Deliberately invalid authority: it discards durable generation state on restart."""

    def restore_state(self, state: Tuple[Tuple[RuntimeSlotId, int], ...]) -> None:
        del state
        self._last_generation = {}
