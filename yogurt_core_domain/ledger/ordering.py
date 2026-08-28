"""Executable ledger ordering rules; terminal turns still accept downstream facts."""

from typing import Tuple

from .facts import InputCommittedRecorded, ResponseAbortedRecorded, ResponseFinalizedRecorded, ResponseStartedRecorded, SegmentCommittedRecorded, TurnCompletedRecorded, _LedgerFact


class LedgerInvariantError(ValueError): pass


def validate_ledger_append(existing_facts: Tuple[_LedgerFact, ...], new_fact: _LedgerFact) -> None:
    turn_facts = tuple(fact for fact in existing_facts if fact.turn_ref == new_fact.turn_ref)
    primary = (ResponseStartedRecorded, SegmentCommittedRecorded, ResponseFinalizedRecorded, ResponseAbortedRecorded)
    if isinstance(new_fact, primary) and not any(isinstance(fact, InputCommittedRecorded) for fact in turn_facts):
        raise LedgerInvariantError("InputCommittedRecorded must precede primary processing facts")
    if isinstance(new_fact, SegmentCommittedRecorded):
        response_id = new_fact.segment.response_id
        if not any(isinstance(fact, ResponseStartedRecorded) and fact.response_started.response_id == response_id for fact in turn_facts):
            raise LedgerInvariantError("ResponseStartedRecorded must precede a segment")
        if any(isinstance(fact, (ResponseFinalizedRecorded, ResponseAbortedRecorded)) for fact in turn_facts):
            raise LedgerInvariantError("cannot append segment after response terminal fact")
        prior = [fact.segment.sequence for fact in turn_facts if isinstance(fact, SegmentCommittedRecorded) and fact.segment.response_id == response_id]
        if prior and new_fact.segment.sequence <= max(prior):
            raise LedgerInvariantError("segment sequence must strictly increase")
        if any(isinstance(fact, TurnCompletedRecorded) for fact in turn_facts):
            raise LedgerInvariantError("TurnCompleted forbids new primary cognitive output")


def append_fact(facts: Tuple[_LedgerFact, ...], fact: _LedgerFact) -> Tuple[_LedgerFact, ...]:
    validate_ledger_append(facts, fact)
    return facts + (fact,)
