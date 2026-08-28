from ..conversation.refs import TurnRef
from ..foundation.idempotency import IdempotencyDecision
from ..foundation.model import FrozenContract
from .turn_snapshot import TurnSnapshot


class TurnAdmission(FrozenContract):
    turn_ref: TurnRef
    snapshot: TurnSnapshot
    idempotency_decision: IdempotencyDecision
