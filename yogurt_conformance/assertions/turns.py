from yogurt_contracts.conversation.fingerprint import TurnFingerprint
from yogurt_contracts.foundation.idempotency import IdempotencyDecision


def assert_turn_retry(existing: TurnFingerprint, retried: TurnFingerprint) -> IdempotencyDecision:
    return IdempotencyDecision.SAME_LOGICAL_REQUEST if existing == retried else IdempotencyDecision.CONFLICT
