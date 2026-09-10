"""Provider-neutral Memory recall boundary."""

from typing import Optional, Protocol

from yogurt_contracts.execution import CapabilityAuthorizationSnapshot
from yogurt_contracts.memory.recall import (
    MemoryRecallBindingSnapshot,
    MemoryRecallCapabilityState,
    MemoryRecallProviderResult,
    MemoryRecallQuery,
)


class MemoryRecallPort(Protocol):
    async def capability_state(self) -> MemoryRecallCapabilityState:
        ...

    async def current_binding(self) -> Optional[MemoryRecallBindingSnapshot]:
        ...

    async def recall(
        self,
        query: MemoryRecallQuery,
        binding: MemoryRecallBindingSnapshot,
        authorization: CapabilityAuthorizationSnapshot,
    ) -> MemoryRecallProviderResult:
        ...
