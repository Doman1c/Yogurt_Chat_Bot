from yogurt_contracts.foundation.durability import DurabilityLevel
from yogurt_contracts.operations.traits import OperationTraits

from ..assertions.operations import assert_dispatch_allowed


def run_operation_provider_conformance(traits: OperationTraits, achieved: DurabilityLevel, required: DurabilityLevel, ephemeral_core: bool = False) -> None:
    assert_dispatch_allowed(traits, achieved, required, ephemeral_core)
