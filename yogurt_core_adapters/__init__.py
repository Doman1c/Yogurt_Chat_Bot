"""Reference-only adapters for the R2 Core."""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .state_store_sqlite import SQLiteCoreStateStore


def __getattr__(name: str) -> Any:
    # Ephemeral Core/model adapters do not require the optional durable
    # operation/Memory implementation. Preserve the public SQLite export.
    if name == "SQLiteCoreStateStore":
        from .state_store_sqlite import SQLiteCoreStateStore
        return SQLiteCoreStateStore
    raise AttributeError(name)

__all__ = ["SQLiteCoreStateStore"]
