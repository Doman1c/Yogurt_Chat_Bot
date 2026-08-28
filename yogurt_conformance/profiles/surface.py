from yogurt_conformance.protocols.adapters import SurfaceAdapterProtocol
from ._scenarios import assert_scenarios


def run_surface_conformance(adapter: SurfaceAdapterProtocol) -> None:
    assert_scenarios(adapter, ("accepted_not_delivered", "delivered_not_presented", "presented_not_read", "duplicate_receipt_idempotent", "conflicting_receipt_rejected", "progress_monotonic", "terminal_preserves_extent"))
