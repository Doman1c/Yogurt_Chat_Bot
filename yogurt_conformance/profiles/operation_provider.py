from yogurt_conformance.protocols.adapters import OperationProviderAdapterProtocol
from ._scenarios import assert_scenarios


def run_operation_provider_conformance(adapter: OperationProviderAdapterProtocol) -> None:
    assert_scenarios(adapter, ("intent_before_dispatch", "same_operation_retry", "operation_conflict", "insufficient_durability_rejected", "ephemeral_side_effect_rejected", "expired_authorization_rejected", "stale_generation_rejected", "historical_assignment_rejected", "unknown_outcome_keeps_operation_id"))
