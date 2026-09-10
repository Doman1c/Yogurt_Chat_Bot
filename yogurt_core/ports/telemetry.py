"""Optional, transport-neutral execution checkpoints; observers cannot affect facts."""
from typing import Protocol
from yogurt_contracts.conversation.refs import ConversationRef
from yogurt_contracts.foundation.identifiers import TurnId

class TurnObserver(Protocol):
    def checkpoint(self, conversation: ConversationRef, turn_id: TurnId, name: str) -> None: ...
