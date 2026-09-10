"""R2-A ephemeral, provider-neutral Yogurt Core reference runtime."""

from typing import TYPE_CHECKING, Any

from .lifecycle import CoreLifecycleState

if TYPE_CHECKING:
    from .application.core import YogurtCore


def __getattr__(name: str) -> Any:
    # Pure Core projections must not load optional application ports on import.
    # Preserve the existing public ``from yogurt_core import YogurtCore`` API.
    if name == "YogurtCore":
        from .application.core import YogurtCore
        return YogurtCore
    raise AttributeError(name)


__all__ = ["CoreLifecycleState", "YogurtCore"]
