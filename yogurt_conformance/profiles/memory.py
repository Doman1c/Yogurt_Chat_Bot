"""Reusable public Memory scope/exposure sequences."""

from yogurt_conformance.protocols.adapters import MemoryAdapterProtocol
from ._scenarios import assert_scenarios


def run_memory_conformance(adapter: MemoryAdapterProtocol) -> None:
    assert_scenarios(adapter, ("scope_preserved", "exact_retry_idempotent", "payload_conflict", "commit_is_not_exposure", "delivery_is_not_exposure", "partial_extent_monotonic", "cross_segment_isolated"))
