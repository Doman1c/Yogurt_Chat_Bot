from dataclasses import dataclass
from typing import Tuple


@dataclass(frozen=True)
class ContextProjection:
    committed_user_inputs: Tuple[str, ...]
    committed_assistant_segments: Tuple[str, ...]
    conversation_metadata: str
