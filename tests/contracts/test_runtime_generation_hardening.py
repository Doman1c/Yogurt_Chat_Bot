"""P0-04 authority-issued generation and bound-provider fencing regression coverage."""

from datetime import datetime, timezone

import pytest

from yogurt_contracts.capability.binding import CapabilityBinding
from yogurt_contracts.foundation.identifiers import BindingId, CapabilityId, GenerationAuthorityId, NodeId, RuntimeInstanceId, RuntimeSlotId
from yogurt_contracts.foundation.time import UtcInstant
from yogurt_contracts.foundation.versioning import ContractVersion
from yogurt_contracts.runtime.fencing import FencingDecision, compare_generation_numbers, validate_bound_provider, validate_runtime_against_current_assignment, validate_runtime_matches_assignment
from yogurt_contracts.runtime.generation import GenerationAssignment
from yogurt_contracts.runtime.identity import RuntimeIdentity


NOW = UtcInstant(value=datetime(2026, 1, 1, tzinfo=timezone.utc))
AUTHORITY = GenerationAuthorityId(value="generation-authority")


def _assignment(slot: str = "voice.mac", instance: str = "instance-new", generation: int = 7, authority: GenerationAuthorityId = AUTHORITY) -> GenerationAssignment:
    return GenerationAssignment(runtime_slot_id=RuntimeSlotId(value=slot), runtime_instance_id=RuntimeInstanceId(value=instance), generation=generation, issued_by=authority, issued_at=NOW, authority_revision="1")


def _runtime(slot: str = "voice.mac", instance: str = "instance-new", generation: int = 7) -> RuntimeIdentity:
    return RuntimeIdentity(runtime_type="voice", runtime_slot_id=RuntimeSlotId(value=slot), runtime_instance_id=RuntimeInstanceId(value=instance), runtime_generation=generation, node_id=NodeId(value="node"))


def _binding(slot: str = "voice.mac", instance: str = "instance-new", generation: int = 7) -> CapabilityBinding:
    return CapabilityBinding(binding_id=BindingId(value="binding"), binding_revision="1", capability_id=CapabilityId(value="voice.speak"), provider_slot_id=RuntimeSlotId(value=slot), provider_instance_id=RuntimeInstanceId(value=instance), provider_generation=generation, contract_version=ContractVersion(major=1, minor=0), authority_valid_until=NOW)


def test_exact_authority_assignment_is_the_only_runtime_fencing_acceptance() -> None:
    assert validate_runtime_against_current_assignment(_runtime(), _assignment(), AUTHORITY) == FencingDecision.ACCEPT
    zombie = _runtime(instance="instance-old", generation=999)
    assert validate_runtime_against_current_assignment(zombie, _assignment(), AUTHORITY) != FencingDecision.ACCEPT
    assert validate_runtime_against_current_assignment(_runtime(instance="instance-other"), _assignment(), AUTHORITY) != FencingDecision.ACCEPT
    assert validate_runtime_against_current_assignment(_runtime(slot="voice.stackchan"), _assignment(), AUTHORITY) != FencingDecision.ACCEPT
    assert validate_runtime_against_current_assignment(_runtime(), _assignment(authority=GenerationAuthorityId(value="wrong-authority")), AUTHORITY) != FencingDecision.ACCEPT


def test_bound_provider_requires_exact_runtime_and_assignment_identity() -> None:
    assert validate_bound_provider(_binding(), _runtime(), _assignment(), AUTHORITY) == FencingDecision.ACCEPT
    assert validate_bound_provider(_binding(generation=6), _runtime(), _assignment(), AUTHORITY) == FencingDecision.STALE_GENERATION
    assert validate_bound_provider(_binding(instance="instance-other"), _runtime(), _assignment(), AUTHORITY) == FencingDecision.BINDING_MISMATCH


def test_numeric_ordering_remains_separate_from_security_fencing() -> None:
    assert compare_generation_numbers("voice.mac", 8, "voice.mac", 7) == FencingDecision.ACCEPT
    assert compare_generation_numbers("voice.stackchan", 3, "voice.mac", 12) == FencingDecision.SLOT_MISMATCH
    assert validate_runtime_against_current_assignment(_runtime(instance="zombie", generation=999), _assignment(), AUTHORITY) != FencingDecision.ACCEPT


def test_historical_assignment_is_not_current_fencing_evidence() -> None:
    old_assignment = _assignment(instance="old-runtime", generation=6)
    old_runtime = _runtime(instance="old-runtime", generation=6)
    old_binding = _binding(instance="old-runtime", generation=6)
    current_assignment = _assignment(instance="new-runtime", generation=7)
    assert validate_runtime_matches_assignment(old_runtime, old_assignment, AUTHORITY) == FencingDecision.ACCEPT
    assert validate_bound_provider(old_binding, old_runtime, current_assignment, AUTHORITY) != FencingDecision.ACCEPT


@pytest.mark.parametrize("constructor", [_assignment, _runtime, _binding])
def test_generation_contracts_reject_zero_or_negative_generations(constructor) -> None:
    with pytest.raises(ValueError):
        constructor(generation=0)
