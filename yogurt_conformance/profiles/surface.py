from yogurt_conformance.protocols.adapters import SurfaceAdapterProtocol


def run_surface_conformance(adapter: SurfaceAdapterProtocol) -> None:
    adapter.accept("receipt-a")
    assert adapter.projection().accepted_receipts and not adapter.projection().delivered_receipts
    assert not adapter.is_read("receipt-a")
    adapter.deliver("receipt-a")
    assert adapter.projection().delivered_receipts and not adapter.is_read("receipt-a")
    adapter.report_text("receipt-a", 10)
    adapter.report_text("receipt-a", 3)
    text_extent = adapter.projection().max_text_extent
    assert text_extent is not None and text_extent.end_text_offset == 10
    adapter.terminal("receipt-a", "completed")
    assert adapter.projection().max_text_extent is not None
    try:
        adapter.terminal("receipt-b", "failed")
    except ValueError:
        pass
    else:
        raise AssertionError("conflicting terminal must be rejected")
