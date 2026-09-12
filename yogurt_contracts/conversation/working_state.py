"""Private, provider-neutral derived conversation cognition; never authority."""

from enum import Enum
from typing import Optional, Tuple

from pydantic import Field, model_validator

from ..foundation.model import FrozenContract
from .refs import ConversationRef, TurnRef


class WorkingStateLifecycle(str, Enum):
    ACTIVE = "active"
    RESOLVED = "resolved"
    EXPIRED = "expired"


class WorkingStateKind(str, Enum):
    THREAD = "thread"
    FOCUS = "focus"
    QUESTION = "question"


class WorkingStateEntry(FrozenContract):
    entry_id: str = Field(min_length=1, max_length=80)
    kind: WorkingStateKind
    value: str = Field(min_length=1, max_length=160)
    source_turn_refs: Tuple[TurnRef, ...] = Field(min_length=1, max_length=3)
    derived_revision: int = Field(ge=1)
    lifecycle: WorkingStateLifecycle

    @model_validator(mode="after")
    def valid_sources(self) -> "WorkingStateEntry":
        if not self.value.strip() or len(set(self.source_turn_refs)) != len(self.source_turn_refs):
            raise ValueError("working state requires visible value and unique sources")
        if len({ref.conversation_id for ref in self.source_turn_refs}) != 1:
            raise ValueError("working state sources must belong to one conversation")
        return self


class ConversationWorkingState(FrozenContract):
    conversation_ref: ConversationRef
    revision: int = Field(ge=0)
    derivation_version: str = Field(min_length=1, max_length=64)
    derived_through_turn: Optional[TurnRef]
    active_threads: Tuple[WorkingStateEntry, ...] = Field(default=(), max_length=5)
    recent_focus: Tuple[WorkingStateEntry, ...] = Field(default=(), max_length=3)
    unresolved_items: Tuple[WorkingStateEntry, ...] = Field(default=(), max_length=5)

    @model_validator(mode="after")
    def internally_consistent(self) -> "ConversationWorkingState":
        entries = self.active_threads + self.recent_focus + self.unresolved_items
        if self.derived_through_turn is None:
            if self.revision != 0 or entries:
                raise ValueError("only empty state may omit through-turn")
        elif self.revision < 1 or self.derived_through_turn.conversation_id != self.conversation_ref.conversation_id:
            raise ValueError("invalid working state boundary")
        if len({entry.entry_id for entry in entries}) != len(entries):
            raise ValueError("working state entry identities must be unique")
        for collection, kind in ((self.active_threads, WorkingStateKind.THREAD),
                                 (self.recent_focus, WorkingStateKind.FOCUS),
                                 (self.unresolved_items, WorkingStateKind.QUESTION)):
            for entry in collection:
                if entry.kind != kind or entry.derived_revision > self.revision:
                    raise ValueError("invalid working state kind or revision")
                if any(ref.conversation_id != self.conversation_ref.conversation_id for ref in entry.source_turn_refs):
                    raise ValueError("working state source conversation mismatch")
        return self
