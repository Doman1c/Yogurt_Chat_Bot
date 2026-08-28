from datetime import datetime, timezone

import pytest
from hypothesis import given, strategies as st

from yogurt_conformance.assertions.operations import assert_dispatch_allowed
from yogurt_conformance.assertions.turns import assert_turn_retry
from yogurt_conformance.profiles.runtime_lifecycle import run_runtime_lifecycle_conformance
from yogurt_contracts.conversation.fingerprint import TurnFingerprint
from yogurt_contracts.conversation.idempotency import TurnIdempotencyKey, TurnRetryDecision
from yogurt_contracts.conversation.refs import ConversationRef, TurnRef
from yogurt_contracts.foundation.identifiers import ConversationId, PersonaId, TurnId, UserId
from yogurt_contracts.foundation.durability import DurabilityLevel
from yogurt_contracts.foundation.idempotency import IdempotencyDecision, RequestFingerprint
from yogurt_contracts.operations.traits import CancellationSupport, EffectKind, IdempotencyKind, OperationTraits, ReversibilityKind
from yogurt_contracts.runtime.fencing import FencingDecision


def _fingerprint(value: str) -> TurnFingerprint:
    digest = RequestFingerprint(value=value)
    return TurnFingerprint(input_digest=digest, origin_digest=digest, principal_binding_digest=digest)


def _key(turn_id: str) -> TurnIdempotencyKey:
    conversation = ConversationRef(persona_id=PersonaId(value="persona"), user_id=UserId(value="user"), conversation_id=ConversationId(value="conversation"))
    return TurnIdempotencyKey(conversation_ref=conversation, turn_ref=TurnRef(conversation_id=conversation.conversation_id, turn_id=TurnId(value=turn_id)))


def test_ephemeral_core_rejects_all_external_effects():
    traits = OperationTraits(effect_kind=EffectKind.PHYSICAL, idempotency=IdempotencyKind.NONE, reversibility=ReversibilityKind.IRREVERSIBLE, cancellation_support=CancellationSupport.BEST_EFFORT)
    with pytest.raises(AssertionError):
        assert_dispatch_allowed(traits, DurabilityLevel.DURABLY_PERSISTED, DurabilityLevel.DURABLY_PERSISTED, True)


def test_real_write_requires_crash_safe_durability():
    traits = OperationTraits(effect_kind=EffectKind.WRITE, idempotency=IdempotencyKind.KEYED, reversibility=ReversibilityKind.COMPENSATABLE, cancellation_support=CancellationSupport.PROVIDER_SUPPORTED)
    with pytest.raises(AssertionError):
        assert_dispatch_allowed(traits, DurabilityLevel.JOURNALED, DurabilityLevel.DURABLY_PERSISTED, False)


@given(st.text(min_size=1, max_size=32).filter(lambda value: all(ord(char) >= 32 and ord(char) != 127 for char in value)))
def test_turn_retry_is_idempotent_for_same_fingerprint(value):
    assert assert_turn_retry(_key("turn"), _fingerprint(value), _key("turn"), _fingerprint(value)) == TurnRetryDecision.SAME_LOGICAL_TURN


@given(st.integers(min_value=0, max_value=100), st.integers(min_value=0, max_value=100))
def test_same_slot_fencing_is_monotonic(candidate, active):
    decision = run_runtime_lifecycle_conformance("voice.mac", candidate, "voice.mac", active)
    assert decision == (FencingDecision.ACCEPT if candidate >= active else FencingDecision.STALE_GENERATION)
