from typing import Tuple

from ..foundation.identifiers import ResponseId, SegmentId
from ..foundation.model import FrozenContract
from ..operations.intent import LogicalOperation
from .target import PresentationTarget


class PresentationOperation(FrozenContract):
    """Presentation-specific view of the shared logical Operation contract."""

    operation: LogicalOperation
    response_id: ResponseId
    segment_ids: Tuple[SegmentId, ...]
    target: PresentationTarget
