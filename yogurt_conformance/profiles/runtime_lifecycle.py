from yogurt_contracts.runtime.fencing import FencingDecision, validate_generation


def run_runtime_lifecycle_conformance(slot: str, generation: int, active_slot: str, active_generation: int) -> FencingDecision:
    return validate_generation(slot, generation, active_slot, active_generation)
