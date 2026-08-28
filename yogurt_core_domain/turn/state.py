from typing import Optional

from yogurt_contracts.conversation.lifecycle import AbortReason, TurnState
from yogurt_contracts.conversation.refs import TurnRef
from yogurt_contracts.foundation.model import FrozenContract


class TurnStateRecord(FrozenContract):
    turn_ref: TurnRef
    state: TurnState
    abort_reason: Optional[AbortReason] = None
