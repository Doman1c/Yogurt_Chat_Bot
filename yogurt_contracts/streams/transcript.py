from typing import Optional

from ..foundation.identifiers import Identifier
from ..foundation.model import FrozenContract


class TranscriptHypothesis(FrozenContract):
    hypothesis_id: Identifier
    revision: int
    text: str
    stability: Optional[float] = None
    is_final: bool = False


def supersedes(new: TranscriptHypothesis, old: TranscriptHypothesis) -> bool:
    return new.hypothesis_id == old.hypothesis_id and new.revision > old.revision
