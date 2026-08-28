"""Behavioral public-Core conformance; the profile owns all assertions."""

from yogurt_contracts.conversation.fingerprint import TurnFingerprint
from yogurt_contracts.conversation.refs import ConversationRef
from yogurt_contracts.foundation.idempotency import RequestFingerprint
from yogurt_contracts.foundation.identifiers import ConversationId, PersonaId, SegmentId, TurnId, UserId
from yogurt_conformance.protocols.adapters import CorePublicAdapterProtocol


def _fingerprint(value: str) -> TurnFingerprint:
    digest = RequestFingerprint(value=value * 64)
    return TurnFingerprint(input_digest=digest, origin_digest=digest, principal_binding_digest=digest)


def run_core_public_conformance(adapter: CorePublicAdapterProtocol) -> None:
    first_conversation = ConversationRef(persona_id=PersonaId(value="persona"), user_id=UserId(value="user"), conversation_id=ConversationId(value="conversation-a"))
    other_conversation = first_conversation.model_copy(update={"conversation_id": ConversationId(value="conversation-b")})
    first = adapter.submit_turn(first_conversation, TurnId(value="turn-a"), _fingerprint("a"))
    retry = adapter.submit_turn(first_conversation, TurnId(value="turn-a"), _fingerprint("a"))
    assert not first.conflict and retry.logical_turn == first.logical_turn
    assert adapter.submit_turn(first_conversation, TurnId(value="turn-a"), _fingerprint("b")).conflict
    assert adapter.submit_turn(other_conversation, TurnId(value="turn-a"), _fingerprint("a")).logical_turn != first.logical_turn
    assert adapter.submit_turn(first_conversation, TurnId(value="turn-b"), _fingerprint("a")).logical_turn != first.logical_turn
    response = adapter.start_response(first.logical_turn)
    assert response is not None and adapter.start_response(first.logical_turn) is None
    segment = SegmentId(value="segment-a")
    assert adapter.append_segment(first.logical_turn, response, segment, "hello")
    assert adapter.append_segment(first.logical_turn, response, segment, "hello")
    assert not adapter.append_segment(first.logical_turn, response, segment, "changed")
    assert adapter.fallback_attempt(first.logical_turn, response) is not None
    assert adapter.finalize_response(first.logical_turn, response)
    assert not adapter.append_segment(first.logical_turn, response, SegmentId(value="segment-b"), "late")
    assert adapter.complete_turn(first.logical_turn)
    assert adapter.record_late_downstream(first.logical_turn)
    assert adapter.start_response(first.logical_turn) is None
