from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from typing import Optional

from .conversation_state import ConversationState, ConversationStateStore


class ConversationStateRuntimeStore(ConversationStateStore):
    """Runtime reducer with completion-first open-thread semantics."""

    def observe_user_turn(
        self,
        user_id: str,
        conversation_key: str,
        message: str,
        *,
        now: Optional[datetime] = None,
    ) -> ConversationState:
        user_id = self._normalize_identifier(user_id, "user_id")
        conversation_key = self._normalize_identifier(conversation_key, "conversation_key")
        if not isinstance(message, str):
            raise TypeError("message must be a string")
        current_time = (now or self._now()).astimezone(timezone.utc)
        raw_before = self._load_raw(user_id, conversation_key, current_time)
        before, expired = self._apply_expiry(raw_before, current_time)

        dialogue_act = self.classify_dialogue_act(message)
        mood = self.classify_mood(message)
        topic = self.topic_for_message(message, before.current_topic)
        interaction_mode = self.classify_interaction_mode(message, dialogue_act, mood)

        completed_threads = set(self._completed_thread_markers(message))
        remaining_threads = tuple(
            item for item in before.open_threads if item not in completed_threads
        )
        new_threads = tuple(
            item
            for item in self._thread_candidates(message, topic)
            if item not in completed_threads
        )
        open_threads = self._merge_bounded(
            remaining_threads,
            new_threads,
            self.max_open_threads,
        )
        recent_actions = self._merge_bounded(
            before.recent_actions,
            self._action_candidates(message),
            self.max_recent_actions,
        )

        after = replace(
            before,
            state_version=raw_before.state_version + 1,
            current_topic=topic,
            dialogue_act=dialogue_act,
            user_mood=mood,
            interaction_mode=interaction_mode,
            open_threads=open_threads,
            recent_actions=recent_actions,
            topic_expires_at=self._iso(current_time + self.topic_ttl),
            mood_expires_at=(
                self._iso(current_time + self.mood_ttl) if mood != "neutral" else None
            ),
            thread_expires_at=(
                self._iso(current_time + self.thread_ttl) if open_threads else None
            ),
            updated_at=self._iso(current_time),
        )
        changed = self._changed_fields(raw_before, after)
        if expired:
            changed["expired_fields"] = list(expired)
        return self._write(raw_before, after, "user_turn", changed)
