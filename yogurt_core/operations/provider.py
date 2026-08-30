from typing import Protocol

from yogurt_contracts.foundation.identifiers import CapabilityId
from yogurt_contracts.operations.intent import OperationIntentCommitted

from .models import ProviderDispatchResult


class OperationProviderPort(Protocol):
    """External boundary; real SDKs stay outside Core and are never imported."""

    @property
    def capability_id(self) -> CapabilityId:
        """The capability this concrete provider can actually execute."""
        ...

    async def dispatch(self, intent: OperationIntentCommitted) -> ProviderDispatchResult:
        ...
