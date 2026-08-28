from enum import Enum

from .generation import GenerationAssignment
from .identity import RuntimeIdentity


class FencingDecision(str, Enum): ACCEPT = "accept"; STALE_GENERATION = "stale_generation"; SLOT_MISMATCH = "slot_mismatch"; ASSIGNMENT_MISMATCH = "assignment_mismatch"


def validate_generation(slot_id: str, generation: int, active_slot_id: str, active_generation: int) -> FencingDecision:
    if slot_id != active_slot_id: return FencingDecision.SLOT_MISMATCH
    return FencingDecision.ACCEPT if generation >= active_generation else FencingDecision.STALE_GENERATION


def validate_runtime_assignment(runtime: RuntimeIdentity, assignment: GenerationAssignment, expected_authority: str) -> FencingDecision:
    if assignment.issued_by.value != expected_authority or runtime.runtime_slot_id != assignment.runtime_slot_id or runtime.runtime_instance_id != assignment.runtime_instance_id or runtime.runtime_generation != assignment.generation:
        return FencingDecision.ASSIGNMENT_MISMATCH
    return FencingDecision.ACCEPT
