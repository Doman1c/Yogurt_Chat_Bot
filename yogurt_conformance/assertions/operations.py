from yogurt_contracts.foundation.durability import DurabilityLevel, durability_satisfies
from yogurt_contracts.operations.traits import OperationTraits


def assert_dispatch_allowed(traits: OperationTraits, achieved: DurabilityLevel, required: DurabilityLevel, ephemeral_core: bool) -> None:
    if traits.is_externally_side_effecting() and ephemeral_core:
        raise AssertionError("Ephemeral Core cannot dispatch externally side-effecting capability")
    if traits.is_externally_side_effecting() and not durability_satisfies(achieved, required):
        raise AssertionError("side effect requires configured durability before dispatch")
