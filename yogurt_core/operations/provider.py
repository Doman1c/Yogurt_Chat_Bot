from typing import Protocol

from yogurt_contracts.operations.intent import OperationIntentCommitted

from .models import ProviderDispatchResult


class OperationProviderPort(Protocol):
    """External boundary; real SDKs stay outside Core and are never imported."""

    async def dispatch(self, intent: OperationIntentCommitted) -> ProviderDispatchResult:
        ...
