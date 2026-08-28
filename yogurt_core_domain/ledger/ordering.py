"""Pure, deterministic invariants for appending CORE_DOMAIN_INTERNAL ledger facts."""

from typing import Optional, Tuple, Union

from yogurt_contracts.foundation.identifiers import ResponseId

from .facts import (
    InputCommittedRecorded,
    ResponseAbortedRecorded,
    ResponseFinalizedRecorded,
    ResponseStartedRecorded,
    SegmentCommittedRecorded,
    TurnAbortedRecorded,
    TurnCompletedRecorded,
    _LedgerFact,
)


class LedgerInvariantError(ValueError):
    """Raised when a proposed authoritative ledger fact breaks a turn invariant."""


ResponseTerminal = Union[ResponseFinalizedRecorded, ResponseAbortedRecorded]
TurnTerminal = Union[TurnCompletedRecorded, TurnAbortedRecorded]
PrimaryFact = Union[ResponseStartedRecorded, SegmentCommittedRecorded, ResponseTerminal]


def _turn_facts(existing_facts: Tuple[_LedgerFact, ...], new_fact: _LedgerFact) -> Tuple[_LedgerFact, ...]:
    if new_fact.turn_ref is None:
        return tuple(fact for fact in existing_facts if fact.turn_ref is None)
    return tuple(
        fact
        for fact in existing_facts
        if fact.turn_ref is not None
        and fact.turn_ref.conversation_id == new_fact.turn_ref.conversation_id
        and fact.turn_ref.turn_id == new_fact.turn_ref.turn_id
    )


def _started_response(turn_facts: Tuple[_LedgerFact, ...], response_id: ResponseId) -> Optional[ResponseStartedRecorded]:
    return next((fact for fact in turn_facts if isinstance(fact, ResponseStartedRecorded) and fact.response_started.response_id == response_id), None)


def _has_input_commit(turn_facts: Tuple[_LedgerFact, ...]) -> bool:
    return any(isinstance(fact, InputCommittedRecorded) for fact in turn_facts)


def _has_turn_terminal(turn_facts: Tuple[_LedgerFact, ...]) -> bool:
    return any(isinstance(fact, (TurnCompletedRecorded, TurnAbortedRecorded)) for fact in turn_facts)


def _terminal_response_id(fact: ResponseTerminal) -> ResponseId:
    if isinstance(fact, ResponseFinalizedRecorded):
        return fact.response_finalized.response_id
    return fact.response_aborted.response_id


def _response_terminal(turn_facts: Tuple[_LedgerFact, ...], response_id: ResponseId) -> Optional[ResponseTerminal]:
    return next((fact for fact in turn_facts if isinstance(fact, (ResponseFinalizedRecorded, ResponseAbortedRecorded)) and _terminal_response_id(fact) == response_id), None)


def _require_primary_turn(new_fact: PrimaryFact) -> None:
    if new_fact.turn_ref is None:
        raise LedgerInvariantError("primary processing facts require an outer turn_ref")


def _validate_input(turn_facts: Tuple[_LedgerFact, ...], new_fact: InputCommittedRecorded) -> None:
    if _has_input_commit(turn_facts):
        raise LedgerInvariantError("a turn may have only one authoritative InputCommittedRecorded")
    if _has_turn_terminal(turn_facts):
        raise LedgerInvariantError("turn terminal facts forbid a new input commit")


def _validate_response_start(turn_facts: Tuple[_LedgerFact, ...], new_fact: ResponseStartedRecorded) -> None:
    _require_primary_turn(new_fact)
    if not _has_input_commit(turn_facts):
        raise LedgerInvariantError("InputCommittedRecorded must precede ResponseStartedRecorded")
    if _has_turn_terminal(turn_facts):
        raise LedgerInvariantError("turn terminal facts forbid a new response start")
    if any(isinstance(fact, ResponseStartedRecorded) for fact in turn_facts):
        raise LedgerInvariantError("a turn may have only one primary logical response")


def _validate_segment(turn_facts: Tuple[_LedgerFact, ...], new_fact: SegmentCommittedRecorded) -> None:
    _require_primary_turn(new_fact)
    if not _has_input_commit(turn_facts):
        raise LedgerInvariantError("InputCommittedRecorded must precede SegmentCommittedRecorded")
    if _has_turn_terminal(turn_facts):
        raise LedgerInvariantError("turn terminal facts forbid a new segment")
    response_id = new_fact.segment.response_id
    if _started_response(turn_facts, response_id) is None:
        raise LedgerInvariantError("ResponseStartedRecorded must precede a segment for the same response")
    if _response_terminal(turn_facts, response_id) is not None:
        raise LedgerInvariantError("cannot append a segment after that response terminal fact")
    prior_sequences = [fact.segment.sequence for fact in turn_facts if isinstance(fact, SegmentCommittedRecorded) and fact.segment.response_id == response_id]
    if prior_sequences and new_fact.segment.sequence <= max(prior_sequences):
        raise LedgerInvariantError("segment sequence must strictly increase per response")


def _validate_response_terminal(turn_facts: Tuple[_LedgerFact, ...], new_fact: ResponseTerminal) -> None:
    _require_primary_turn(new_fact)
    if not _has_input_commit(turn_facts):
        raise LedgerInvariantError("InputCommittedRecorded must precede a response terminal fact")
    if _has_turn_terminal(turn_facts):
        raise LedgerInvariantError("turn terminal facts forbid a response terminal fact")
    response_id = _terminal_response_id(new_fact)
    if _started_response(turn_facts, response_id) is None:
        raise LedgerInvariantError("ResponseStartedRecorded must precede a terminal fact for the same response")
    if _response_terminal(turn_facts, response_id) is not None:
        raise LedgerInvariantError("a response may have only one authoritative terminal fact")


def _validate_turn_terminal(turn_facts: Tuple[_LedgerFact, ...], new_fact: TurnTerminal) -> None:
    if not _has_input_commit(turn_facts):
        raise LedgerInvariantError("InputCommittedRecorded must precede a turn terminal fact")
    if _has_turn_terminal(turn_facts):
        raise LedgerInvariantError("a turn may have only one authoritative terminal fact")
    if isinstance(new_fact, TurnCompletedRecorded):
        started = next((fact for fact in turn_facts if isinstance(fact, ResponseStartedRecorded)), None)
        if started is not None and _response_terminal(turn_facts, started.response_started.response_id) is None:
            raise LedgerInvariantError("TurnCompletedRecorded requires the started response to be terminal")


def validate_ledger_append(existing_facts: Tuple[_LedgerFact, ...], new_fact: _LedgerFact) -> None:
    """Validate a single append without mutation or process-local state."""

    turn_facts = _turn_facts(existing_facts, new_fact)
    if isinstance(new_fact, InputCommittedRecorded):
        _validate_input(turn_facts, new_fact)
    elif isinstance(new_fact, ResponseStartedRecorded):
        _validate_response_start(turn_facts, new_fact)
    elif isinstance(new_fact, SegmentCommittedRecorded):
        _validate_segment(turn_facts, new_fact)
    elif isinstance(new_fact, (ResponseFinalizedRecorded, ResponseAbortedRecorded)):
        _validate_response_terminal(turn_facts, new_fact)
    elif isinstance(new_fact, (TurnCompletedRecorded, TurnAbortedRecorded)):
        _validate_turn_terminal(turn_facts, new_fact)


def append_fact(facts: Tuple[_LedgerFact, ...], fact: _LedgerFact) -> Tuple[_LedgerFact, ...]:
    validate_ledger_append(facts, fact)
    return facts + (fact,)
