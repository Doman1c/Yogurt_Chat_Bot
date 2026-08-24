"""Yogurt's channel-independent singing cover capability."""

from .assets import SingingAssetRegistry
from .events import SingingEvent
from .schemas import CoverRequest, SingingResult
from .service import SingingHandle, SingingService

__all__ = [
    "CoverRequest",
    "SingingAssetRegistry",
    "SingingEvent",
    "SingingHandle",
    "SingingResult",
    "SingingService",
]
