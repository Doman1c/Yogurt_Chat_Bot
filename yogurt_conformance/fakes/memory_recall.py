"""Deterministic conformance fake for the optional R3-A Memory recall capability."""

from typing import Optional, Tuple

from yogurt_contracts.execution import CapabilityAuthorizationSnapshot
from yogurt_contracts.memory.recall import (
    MemoryRecallBindingSnapshot,
    MemoryRecallCapabilityState,
    MemoryRecallProviderResult,
    MemoryRecallQuery,
)


class FakeMemoryRecallPort:
    def __init__(
        self,
        state: MemoryRecallCapabilityState,
        binding: Optional[MemoryRecallBindingSnapshot],
        result: MemoryRecallProviderResult,
    ) -> None:
        self.state = state
        self.binding = binding
        self.result = result
        self.call_count = 0
        self.received_queries = ()  # type: Tuple[MemoryRecallQuery, ...]
        self.received_bindings = ()  # type: Tuple[MemoryRecallBindingSnapshot, ...]
        self.received_authorizations = ()  # type: Tuple[CapabilityAuthorizationSnapshot, ...]

    async def capability_state(self) -> MemoryRecallCapabilityState:
        return self.state

    async def current_binding(self) -> Optional[MemoryRecallBindingSnapshot]:
        return self.binding

    async def recall(
        self,
        query: MemoryRecallQuery,
        binding: MemoryRecallBindingSnapshot,
        authorization: CapabilityAuthorizationSnapshot,
    ) -> MemoryRecallProviderResult:
        self.call_count += 1
        self.received_queries += (query,)
        self.received_bindings += (binding,)
        self.received_authorizations += (authorization,)
        return self.result
