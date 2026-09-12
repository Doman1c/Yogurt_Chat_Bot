"""Adapter over the existing Legacy V4 engine, with explicit replay time only."""

import importlib.util
import sys
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Protocol, cast

from .contracts import LegacyConversationStateView, LegacyOnlyFieldPresence, LegacyReplayConfig


class _LegacyStore(Protocol):
    def observe_user_turn(self, user_id: str, conversation_key: str, message: str, *, now: datetime) -> object: ...
    def observe_assistant_turn(self, user_id: str, conversation_key: str, assistant_reply: str, *, now: datetime) -> object: ...
    def list_states(self, user_id: str, *, now: datetime) -> List[Dict[str, object]]: ...


def _factory() -> Callable[..., _LegacyStore]:
    # Load the real standalone source file. Importing legacy's package __init__
    # would eagerly load unrelated Memory/service modules, which W2 does not use.
    # No implementation is copied, mocked, patched, or reclassified here.
    name = "_yogurt_w2_legacy_conversation_state"
    module = sys.modules.get(name)
    if module is None:
        path = Path(__file__).resolve().parents[2] / "social_layer/src/yogurt_social/conversation_state.py"
        spec = importlib.util.spec_from_file_location(name, path)
        if spec is None or spec.loader is None:
            raise RuntimeError("legacy replay implementation unavailable")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return cast(Callable[..., _LegacyStore], module.ConversationStateStore)


class LegacyReplayAdapter:
    """Only instantiated with a fresh DB inside replay's owned TemporaryDirectory."""

    def __init__(self, path: Path, config: LegacyReplayConfig) -> None:
        if path.exists():
            raise ValueError("legacy replay requires a fresh database")
        self._store = _factory()(path, topic_ttl_minutes=config.topic_ttl_minutes,
                                 mood_ttl_minutes=config.mood_ttl_minutes,
                                 thread_ttl_minutes=config.thread_ttl_minutes)

    def observe_user(self, text: str, now: datetime) -> None:
        self._store.observe_user_turn("replay-user", "replay-conversation", text, now=now)

    def observe_assistant(self, text: str, now: datetime) -> None:
        self._store.observe_assistant_turn("replay-user", "replay-conversation", text, now=now)

    def view(self, now: datetime) -> LegacyConversationStateView:
        # list_states applies the real expiry implementation and reports expired
        # fields, without persisting TTL reads or requiring copied expiry logic.
        states = self._store.list_states("replay-user", now=now)
        if not states:
            return LegacyConversationStateView(state_version=0, focus_present=False, thread_count=0)
        if len(states) != 1:
            raise ValueError("unexpected legacy replay scope")
        state = states[0]
        threads, expired = state["open_threads"], state["expired_fields"]
        if not isinstance(threads, list) or not isinstance(expired, list):
            raise ValueError("invalid legacy view")
        return LegacyConversationStateView(
            state_version=int(str(state["state_version"])),
            focus_present=bool(state["current_topic"]), thread_count=len(threads),
            topic_expired="current_topic" in expired, threads_expired="open_threads" in expired,
            legacy_only_fields=LegacyOnlyFieldPresence(
                dialogue_act_present=bool(state["dialogue_act"]),
                non_neutral_mood_present=bool(state["user_mood"]) and state["user_mood"] != "neutral",
                non_default_interaction_mode_present=bool(state["interaction_mode"]) and state["interaction_mode"] != "casual",
                recent_actions_present=bool(state["recent_actions"]),
                last_response_pattern_present=bool(state["last_response_pattern"])),
        )
