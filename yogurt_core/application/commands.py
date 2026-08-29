"""Transport-facing commands.  Every external read carries a caller context."""

from typing import Optional

from pydantic import field_validator

from yogurt_contracts.conversation.input import TurnInput
from yogurt_contracts.conversation.refs import ConversationRef
from yogurt_contracts.conversation.fingerprint import TurnFingerprint
from yogurt_contracts.foundation.identifiers import ResponseId, TurnId, UserId
from yogurt_contracts.foundation.model import FrozenContract
from yogurt_contracts.identity.principal import ExternalPrincipal


class CallerContext(FrozenContract):
    logical_user_id: UserId


class SubmitTurnCommand(FrozenContract):
    caller_context: CallerContext
    external_principal: ExternalPrincipal
    conversation_ref: ConversationRef
    turn_id: TurnId
    fingerprint: TurnFingerprint
    input: TurnInput
    core_config_revision: str
    conversation_context_revision: str
    principal_binding_reference: str


class WatchResponseCommand(FrozenContract):
    caller_context: CallerContext
    response_id: ResponseId
    after_event_sequence: int = 0

    @field_validator("after_event_sequence")
    @classmethod
    def non_negative(cls, value: int) -> int:
        if value < 0:
            raise ValueError("after_event_sequence must be non-negative")
        return value


class GetResponseCommand(FrozenContract):
    caller_context: CallerContext
    response_id: ResponseId


class GetTurnCommand(FrozenContract):
    caller_context: CallerContext
    conversation_ref: ConversationRef
    turn_id: TurnId
