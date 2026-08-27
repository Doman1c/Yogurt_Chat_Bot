"""P11-A1's explicit native Memory seam; production Memory is disabled."""

from __future__ import annotations

from dataclasses import dataclass

from .schemas import native_conversation_key


@dataclass(frozen=True)
class DisabledNativeMemory:
    """No-op adapter that deliberately imports no production Memory client."""

    mode: str = "off"

    def __post_init__(self) -> None:
        if self.mode != "off":
            raise ValueError("native_memory_not_available")

    def scope(self, session_id: str) -> str:
        return native_conversation_key(session_id)

    async def recall(self, session_id: str, text: str) -> None:
        del session_id, text
        return None
