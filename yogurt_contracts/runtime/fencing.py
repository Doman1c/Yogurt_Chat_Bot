"""Generation comparison and authority-issued fencing are deliberately separate contracts."""

from enum import Enum

from ..capability.binding import CapabilityBinding
from ..foundation.identifiers import GenerationAuthorityId
from .generation import GenerationAssignment
from .identity import RuntimeIdentity


class FencingDecision(str, Enum):
    ACCEPT = "accept"
    STALE_GENERATION = "stale_generation"
    SLOT_MISMATCH = "slot_mismatch"
    ASSIGNMENT_MISMATCH = "assignment_mismatch"
    BINDING_MISMATCH = "binding_mismatch"


def compare_generation_numbers(slot_id: str, generation: int, active_slot_id: str, active_generation: int) -> FencingDecision:
    """Pure ordering helper only; it is not a fencing authorization decision."""

    if slot_id != active_slot_id:
        return FencingDecision.SLOT_MISMATCH
    return FencingDecision.ACCEPT if generation >= active_generation else FencingDecision.STALE_GENERATION


def validate_runtime_assignment(
    runtime: RuntimeIdentity,
    assignment: GenerationAssignment,
    expected_authority: GenerationAuthorityId,
) -> FencingDecision:
    """Accept only the exact assignment issued by the expected generation authority."""

    if assignment.issued_by != expected_authority:
        return FencingDecision.ASSIGNMENT_MISMATCH
    if runtime.runtime_slot_id != assignment.runtime_slot_id:
        return FencingDecision.ASSIGNMENT_MISMATCH
    if runtime.runtime_instance_id != assignment.runtime_instance_id:
        return FencingDecision.ASSIGNMENT_MISMATCH
    if runtime.runtime_generation != assignment.generation:
        return FencingDecision.STALE_GENERATION
    return FencingDecision.ACCEPT


def validate_bound_provider(
    binding: CapabilityBinding,
    runtime: RuntimeIdentity,
    assignment: GenerationAssignment,
    expected_authority: GenerationAuthorityId,
) -> FencingDecision:
    """Bind dispatch eligibility to the provider identity and its authority-issued generation."""

    assignment_decision = validate_runtime_assignment(runtime, assignment, expected_authority)
    if assignment_decision != FencingDecision.ACCEPT:
        return assignment_decision
    if binding.provider_slot_id != runtime.runtime_slot_id:
        return FencingDecision.BINDING_MISMATCH
    if binding.provider_instance_id != runtime.runtime_instance_id:
        return FencingDecision.BINDING_MISMATCH
    if binding.provider_generation != runtime.runtime_generation:
        return FencingDecision.STALE_GENERATION
    return FencingDecision.ACCEPT
