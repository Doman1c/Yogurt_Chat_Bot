"""Reusable capability provider compatibility and binding sequences."""

from yogurt_conformance.protocols.adapters import CapabilityProviderAdapterProtocol
from ._scenarios import assert_scenarios


def run_capability_provider_conformance(adapter: CapabilityProviderAdapterProtocol) -> None:
    assert_scenarios(adapter, ("external_typed_descriptor", "offer_binding_authorization_distinct", "major_mismatch_rejected", "minor_too_old_rejected", "stale_generation_rejected", "expired_binding_rejected"))
