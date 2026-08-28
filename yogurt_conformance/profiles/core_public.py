"""Reusable public-Core sequences for future Core implementations."""

from yogurt_conformance.protocols.adapters import CorePublicAdapterProtocol
from ._scenarios import assert_scenarios


def run_core_public_conformance(adapter: CorePublicAdapterProtocol) -> None:
    assert_scenarios(adapter, ("turn_retry_idempotent", "turn_conflict", "one_primary_response", "immutable_committed_segment", "closed_response_rejects_segment", "late_downstream_after_turn_complete"))
