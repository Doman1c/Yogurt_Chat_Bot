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


def validate_runtime_matches_assignment(
    runtime: RuntimeIdentity,
    assignment: GenerationAssignment,
    expected_authority: GenerationAuthorityId,
) -> FencingDecision:
    """Low-level exact-match helper; it does not establish that an assignment is current."""

    if assignment.issued_by != expected_authority:
        return FencingDecision.ASSIGNMENT_MISMATCH
    if runtime.runtime_slot_id != assignment.runtime_slot_id:
        return FencingDecision.ASSIGNMENT_MISMATCH
    if runtime.runtime_instance_id != assignment.runtime_instance_id:
        return FencingDecision.ASSIGNMENT_MISMATCH
    if runtime.runtime_generation != assignment.generation:
        return FencingDecision.STALE_GENERATION
    return FencingDecision.ACCEPT


def validate_runtime_against_current_assignment(
    runtime: RuntimeIdentity,
    current_assignment: GenerationAssignment,
    expected_authority: GenerationAuthorityId,
) -> FencingDecision:
    """Security boundary: compare a runtime only with management-plane current assignment."""

    return validate_runtime_matches_assignment(runtime, current_assignment, expected_authority)


def validate_bound_provider(
    binding: CapabilityBinding,
    runtime: RuntimeIdentity,
    current_assignment: GenerationAssignment,
    expected_authority: GenerationAuthorityId,
) -> FencingDecision:
    """Bind dispatch eligibility to the provider identity and its authority-issued generation."""

    assignment_decision = validate_runtime_against_current_assignment(runtime, current_assignment, expected_authority)
    if assignment_decision != FencingDecision.ACCEPT:
        return assignment_decision
    if binding.provider_slot_id != runtime.runtime_slot_id:
        return FencingDecision.BINDING_MISMATCH
    if binding.provider_instance_id != runtime.runtime_instance_id:
        return FencingDecision.BINDING_MISMATCH
    if binding.provider_generation != runtime.runtime_generation:
        return FencingDecision.STALE_GENERATION
    return FencingDecision.ACCEPT
