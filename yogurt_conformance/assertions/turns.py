from yogurt_contracts.conversation.fingerprint import TurnFingerprint
from yogurt_contracts.conversation.idempotency import TurnIdempotencyKey, TurnRetryDecision


def assert_turn_retry(existing_key: TurnIdempotencyKey, existing: TurnFingerprint, retry_key: TurnIdempotencyKey, retried: TurnFingerprint) -> TurnRetryDecision:
    if existing_key != retry_key:
        return TurnRetryDecision.DIFFERENT_TURN
    return TurnRetryDecision.SAME_LOGICAL_TURN if existing == retried else TurnRetryDecision.TURN_ID_CONFLICT
