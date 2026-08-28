from typing import Optional

from pydantic import model_validator

from yogurt_contracts.conversation.lifecycle import AbortReason, TurnState
from yogurt_contracts.conversation.refs import TurnRef
from yogurt_contracts.foundation.model import FrozenContract


class TurnStateRecord(FrozenContract):
    turn_ref: TurnRef
    state: TurnState
    abort_reason: Optional[AbortReason] = None

    @model_validator(mode="after")
    def has_consistent_abort_reason(self) -> "TurnStateRecord":
        if self.state == TurnState.ABORTED and self.abort_reason is None:
            raise ValueError("aborted turn state requires abort_reason")
        if self.state != TurnState.ABORTED and self.abort_reason is not None:
            raise ValueError("non-aborted turn state forbids abort_reason")
        return self
