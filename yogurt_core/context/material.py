"""Execution-local, user-authority data approved by Core context policy."""

from dataclasses import dataclass

from yogurt_contracts.conversation.refs import TurnRef


@dataclass(frozen=True)
class WorkingStateContextMaterial:
    derivation_version: str
    revision: int
    derived_through_turn: TurnRef
    material: str
