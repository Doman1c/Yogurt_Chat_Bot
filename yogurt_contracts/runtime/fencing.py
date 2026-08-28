from enum import Enum


class FencingDecision(str, Enum): ACCEPT = "accept"; STALE_GENERATION = "stale_generation"; SLOT_MISMATCH = "slot_mismatch"


def validate_generation(slot_id: str, generation: int, active_slot_id: str, active_generation: int) -> FencingDecision:
    if slot_id != active_slot_id: return FencingDecision.SLOT_MISMATCH
    return FencingDecision.ACCEPT if generation >= active_generation else FencingDecision.STALE_GENERATION
