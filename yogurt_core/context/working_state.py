"""On-demand working-state-v1. No clock, provider, cache or model invocation.

Source order is durable turn admission order, not TurnId lexical order. Revision
is 2 * admitted turn count + finalized primary response count at the boundary.
All entries are derived at that revision. Normalization is NFC plus whitespace
collapse (including CRLF), case-sensitive, with 160-codepoint excerpts. Thread
keys hash the FULL normalized input, so truncated prefixes never merge topics.
Threads retain their first-source identity while retained, newest occurrence
first; sources retain the last three occurrences. After >3 admitted turns of
inactivity they expire. Only five most recent threads/questions are retained;
evicted threads start a new identity on recurrence. Focus retains last 3 texts.
Question resolution means execution completion, not semantic correctness.
Unknown derivation versions are rejected, never silently reinterpreted as v1.
"""

import hashlib
import json
import unicodedata
from dataclasses import dataclass
from typing import Dict, Optional, Protocol, Tuple

from yogurt_contracts.conversation.refs import ConversationRef, TurnRef
from yogurt_contracts.conversation.working_state import (
    ConversationWorkingState, WorkingStateEntry, WorkingStateKind, WorkingStateLifecycle,
)


DERIVATION_VERSION = "working-state-v1"
THREAD_EXPIRY_TURNS = 3


@dataclass(frozen=True)
class WorkingStateTurnSource:
    """Internal read-port output, supplied only by authoritative Core storage."""

    turn_ref: TurnRef
    text: str
    primary_response_finalized: bool


class WorkingStateSource(Protocol):
    async def working_state_sources(
        self, conversation_ref: ConversationRef, through_turn_ref: Optional[TurnRef],
    ) -> Tuple[WorkingStateTurnSource, ...]: ...


def normalize_working_text(text: str) -> str:
    return " ".join(unicodedata.normalize("NFC", text).split())


class WorkingStateDeriver:
    def __init__(self, derivation_version: str = DERIVATION_VERSION) -> None:
        if derivation_version != DERIVATION_VERSION:
            raise ValueError("UNSUPPORTED_WORKING_STATE_DERIVATION_VERSION")
        self.version = derivation_version

    def derive(
        self, conversation_ref: ConversationRef, through_turn_ref: Optional[TurnRef],
        sources: Tuple[WorkingStateTurnSource, ...],
    ) -> ConversationWorkingState:
        refs = tuple(source.turn_ref for source in sources)
        if (bool(sources) != (through_turn_ref is not None)
                or (sources and refs[-1] != through_turn_ref)
                or len(set(refs)) != len(refs)
                or any(ref.conversation_id != conversation_ref.conversation_id for ref in refs)):
            raise ValueError("WORKING_STATE_SOURCE_BOUNDARY_MISMATCH")
        revision = 2 * len(sources) + sum(source.primary_response_finalized for source in sources)
        focus: Tuple[WorkingStateEntry, ...] = ()
        questions: Tuple[WorkingStateEntry, ...] = ()
        threads: Dict[str, Tuple[WorkingStateEntry, int]] = {}

        def entry(kind: WorkingStateKind, text: str, source: WorkingStateTurnSource,
                  lifecycle: WorkingStateLifecycle) -> WorkingStateEntry:
            identity = json.dumps((self.version, conversation_ref.model_dump(mode="json"),
                                   kind.value, text, source.turn_ref.model_dump(mode="json")),
                                  ensure_ascii=True, sort_keys=True, separators=(",", ":"))
            return WorkingStateEntry(entry_id=hashlib.sha256(identity.encode("utf-8")).hexdigest(),
                                     kind=kind, value=text[:160], source_turn_refs=(source.turn_ref,),
                                     derived_revision=revision, lifecycle=lifecycle)

        for ordinal, source in enumerate(sources, 1):
            text = normalize_working_text(source.text)
            if not text:
                continue
            focus = (entry(WorkingStateKind.FOCUS, text, source, WorkingStateLifecycle.ACTIVE),) + focus[:2]
            if "?" in text or "？" in text:
                lifecycle = WorkingStateLifecycle.RESOLVED if source.primary_response_finalized else WorkingStateLifecycle.ACTIVE
                questions = (entry(WorkingStateKind.QUESTION, text, source, lifecycle),) + questions[:4]
            key = hashlib.sha256(text.encode("utf-8")).hexdigest()
            previous = threads.pop(key, None)
            thread = entry(WorkingStateKind.THREAD, text, source, WorkingStateLifecycle.ACTIVE)
            if previous is not None:
                thread = previous[0].model_copy(update={"source_turn_refs": (previous[0].source_turn_refs + (source.turn_ref,))[-3:]})
            threads[key] = (thread, ordinal)
            if len(threads) > 5:
                del threads[next(iter(threads))]
        active_threads = tuple(
            thread.model_copy(update={"lifecycle": WorkingStateLifecycle.EXPIRED
                                      if len(sources) - ordinal > THREAD_EXPIRY_TURNS else WorkingStateLifecycle.ACTIVE})
            for thread, ordinal in reversed(tuple(threads.values()))
        )
        return ConversationWorkingState(conversation_ref=conversation_ref, revision=revision,
                                        derivation_version=self.version, derived_through_turn=through_turn_ref,
                                        active_threads=active_threads, recent_focus=focus, unresolved_items=questions)


class WorkingStateProjectionService:
    def __init__(self, source: WorkingStateSource, derivation_version: str = DERIVATION_VERSION) -> None:
        self._source = source
        self._deriver = WorkingStateDeriver(derivation_version)

    async def project(self, conversation_ref: ConversationRef,
                      through_turn_ref: Optional[TurnRef]) -> ConversationWorkingState:
        sources = await self._source.working_state_sources(conversation_ref, through_turn_ref)
        return self._deriver.derive(conversation_ref, through_turn_ref, sources)
