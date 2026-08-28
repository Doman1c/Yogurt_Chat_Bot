from typing import Tuple

from yogurt_contracts.foundation.identifiers import OperationId


class FakeOperationProvider:
    def __init__(self) -> None:
        self.dispatched: Tuple[OperationId, ...] = ()

    def dispatch(self, operation_id: OperationId) -> None:
        self.dispatched += (operation_id,)


class FakeSurfaceAdapter:
    def __init__(self) -> None:
        self.receipt_ids: Tuple[str, ...] = ()
