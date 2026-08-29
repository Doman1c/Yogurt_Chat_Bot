"""Single deterministic mapping from a response terminal fact to its Turn result."""

from dataclasses import dataclass
from typing import Optional

from yogurt_contracts.conversation.lifecycle import AbortReason, TurnState
from yogurt_contracts.response.lifecycle import ResponseAbortReason, ResponseState


@dataclass(frozen=True)
class TurnTerminalConsequence:
    state: TurnState
    abort_reason: Optional[AbortReason] = None


def consequence_from_response_terminal(
    response_state: ResponseState, response_abort_reason: Optional[ResponseAbortReason]
) -> TurnTerminalConsequence:
    if response_state == ResponseState.FINALIZED:
        return TurnTerminalConsequence(state=TurnState.COMPLETED)
    if response_state != ResponseState.ABORTED or response_abort_reason is None:
        raise ValueError("response is not terminal with a typed terminal cause")
    if response_abort_reason == ResponseAbortReason.CANCELLED:
        return TurnTerminalConsequence(state=TurnState.CANCELLED)
    return TurnTerminalConsequence(state=TurnState.ABORTED, abort_reason=AbortReason(response_abort_reason.value))
