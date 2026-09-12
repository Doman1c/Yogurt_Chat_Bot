from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .core import YogurtCore


def __getattr__(name: str) -> Any:
    if name == "YogurtCore":
        from .core import YogurtCore
        return YogurtCore
    raise AttributeError(name)

__all__ = ["YogurtCore"]
