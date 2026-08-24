"""Yogurt's standalone speech capability."""

from .schemas import SpeakRequest, SpeakResult
from .service import MouthService

__all__ = ["MouthService", "SpeakRequest", "SpeakResult"]
