"""Stateful R1 regression suites; all are contract-only and deterministic."""

from hypothesis import settings, strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, precondition, rule, run_state_machine_as_test

from yogurt_conformance.fakes.conformance_adapters import GoodCorePublicFake, GoodOperationProviderFake, GoodRuntimeLifecycleFake, GoodVoiceFake
from yogurt_conformance.profiles.operation_provider import AUTHORITY, _typed_inputs
from yogurt_contracts.foundation.durability import CorePersistenceMode, DurabilityLevel
from yogurt_contracts.conversation.fingerprint import TurnFingerprint
from yogurt_contracts.conversation.refs import ConversationRef
from yogurt_contracts.foundation.idempotency import RequestFingerprint
from yogurt_contracts.foundation.identifiers import ConversationId, OperationId, PersonaId, SegmentId, TurnId, UserId


def _fingerprint() -> TurnFingerprint:
    digest = RequestFingerprint(value="a" * 64)
    return TurnFingerprint(input_digest=digest, origin_digest=digest, principal_binding_digest=digest)


CONVERSATION = ConversationRef(persona_id=PersonaId(value="persona"), user_id=UserId(value="user"), conversation_id=ConversationId(value="conversation"))


class TurnStateMachine(RuleBasedStateMachine):
    def __init__(self) -> None:
        super().__init__(); self.adapter = GoodCorePublicFake(); self.turn = None; self.response = None; self.completed = False
    @rule()
    def commit_input(self) -> None:
        self.turn = self.adapter.submit_turn(CONVERSATION, TurnId(value="turn"), _fingerprint()).logical_turn
    @precondition(lambda self: self.turn is not None and self.response is None)
    @rule()
    def start_response(self) -> None: self.response = self.adapter.start_response(self.turn)
    @precondition(lambda self: self.turn is not None and self.response is not None and not self.completed)
    @rule()
    def finalize_and_complete(self) -> None:
        assert self.adapter.finalize_response(self.turn, self.response); self.completed = self.adapter.complete_turn(self.turn)
    @invariant()
    def completed_forbids_primary_output(self) -> None:
        if self.completed:
            assert self.adapter.start_response(self.turn) is None


class ResponseStateMachine(RuleBasedStateMachine):
    def __init__(self) -> None:
        super().__init__(); self.adapter = GoodCorePublicFake(); self.turn = self.adapter.submit_turn(CONVERSATION, TurnId(value="response-turn"), _fingerprint()).logical_turn; self.response = self.adapter.start_response(self.turn); self.closed = False
    @precondition(lambda self: not self.closed)
    @rule()
    def append(self) -> None: assert self.response is not None and self.adapter.append_segment(self.turn, self.response, SegmentId(value="segment"), "x")
    @precondition(lambda self: not self.closed)
    @rule()
    def finalize(self) -> None:
        assert self.response is not None; self.closed = self.adapter.finalize_response(self.turn, self.response)
    @rule()
    def observe(self) -> None: pass
    @invariant()
    def terminal_forbids_segments(self) -> None:
        if self.closed:
            assert self.response is not None and not self.adapter.append_segment(self.turn, self.response, SegmentId(value="late"), "x")


class PresentationStateMachine(RuleBasedStateMachine):
    def __init__(self) -> None:
        super().__init__(); self.adapter = GoodVoiceFake(); self.segment = SegmentId(value="presentation"); self.adapter.submit_segment(self.segment, True); self.duration = 0; self.offset = 0
    @rule(duration_ms=st.integers(min_value=0, max_value=5000), offset=st.integers(min_value=0, max_value=100))
    def progress(self, duration_ms: int, offset: int) -> None:
        state = self.adapter.report_progress(self.segment, duration_ms, offset); self.duration = max(self.duration, duration_ms); self.offset = max(self.offset, offset); assert state.extent is not None and (state.extent.played_duration_ms, state.extent.end_text_offset) == (self.duration, self.offset)


class OperationStateMachine(RuleBasedStateMachine):
    def __init__(self) -> None:
        super().__init__(); self.adapter = GoodOperationProviderFake(); self.intent, self.authorization, self.runtime, self.assignment = _typed_inputs(); self.operation = self.intent.operation.operation_id; self.committed = False; self.attempts = 0
    @rule()
    def commit_intent(self) -> None: self.adapter.commit_intent(self.intent); self.committed = True
    @precondition(lambda self: self.committed)
    @rule()
    def dispatch_attempt(self) -> None: self.attempts = len(self.adapter.dispatch(self.intent, self.authorization, self.runtime, self.assignment, AUTHORITY, DurabilityLevel.DURABLY_PERSISTED, CorePersistenceMode.DURABLE).attempts)
    @precondition(lambda self: self.committed)
    @rule()
    def unknown_outcome(self) -> None: assert self.adapter.unknown_outcome(self.operation).outcome_unknown
    @invariant()
    def attempts_are_unique(self) -> None:
        if self.committed:
            attempts = self.adapter.commit_intent(self.intent).attempts
            assert len(attempts) == len(set(attempts))


class GenerationStateMachine(RuleBasedStateMachine):
    def __init__(self) -> None:
        super().__init__(); self.adapter = GoodRuntimeLifecycleFake(); self.last = {"a": 0, "b": 0}
    @rule()
    def restart_a(self) -> None:
        identity = self.adapter.start("a", "instance-a"); self.last["a"] += 1; assert identity.runtime_generation == self.last["a"]
    @rule()
    def restart_b(self) -> None:
        identity = self.adapter.start("b", "instance-b"); self.last["b"] += 1; assert identity.runtime_generation == self.last["b"]


def test_turn_state_machine() -> None: run_state_machine_as_test(TurnStateMachine, settings=settings(max_examples=10, stateful_step_count=10))
def test_response_state_machine() -> None: run_state_machine_as_test(ResponseStateMachine, settings=settings(max_examples=10, stateful_step_count=10))
def test_presentation_state_machine() -> None: run_state_machine_as_test(PresentationStateMachine, settings=settings(max_examples=10, stateful_step_count=10))
def test_operation_state_machine() -> None: run_state_machine_as_test(OperationStateMachine, settings=settings(max_examples=10, stateful_step_count=10))
def test_generation_state_machine() -> None: run_state_machine_as_test(GenerationStateMachine, settings=settings(max_examples=10, stateful_step_count=10))
