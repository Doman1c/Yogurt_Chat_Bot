"""Provider-neutral ports used by the application core."""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .memory_recall import MemoryRecallPort
    from .memory_observation import MemoryCausalStateStore


def __getattr__(name: str) -> Any:
    # Core execution/context ports must not eagerly load optional Memory ports.
    if name == "MemoryRecallPort":
        from .memory_recall import MemoryRecallPort
        return MemoryRecallPort
    if name == "MemoryCausalStateStore":
        from .memory_observation import MemoryCausalStateStore
        return MemoryCausalStateStore
    raise AttributeError(name)

__all__ = ["MemoryCausalStateStore", "MemoryRecallPort"]
