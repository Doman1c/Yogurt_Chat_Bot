from __future__ import annotations

from pathlib import Path
from typing import Any

from .conversation_state import ConversationStateStore
from .memory import MemoryStore
from .models import MemorySnippet
from .router import StyleRouter


class SocialLayerService:
    def __init__(
        self,
        db_path: str | Path,
        max_memories_per_reply: int = 3,
        *,
        conversation_state_enabled: bool = False,
        conversation_state_topic_ttl_minutes: int = 60,
        conversation_state_mood_ttl_minutes: int = 30,
        conversation_state_thread_ttl_minutes: int = 1440,
        conversation_state_max_open_threads: int = 5,
        conversation_state_max_recent_actions: int = 6,
    ) -> None:
        self.memory = MemoryStore(db_path)
        self.router = StyleRouter()
        self.max_memories_per_reply = max_memories_per_reply
        self.conversation_state_enabled = conversation_state_enabled
        self.conversation_state = (
            ConversationStateStore(
                db_path,
                topic_ttl_minutes=conversation_state_topic_ttl_minutes,
                mood_ttl_minutes=conversation_state_mood_ttl_minutes,
                thread_ttl_minutes=conversation_state_thread_ttl_minutes,
                max_open_threads=conversation_state_max_open_threads,
                max_recent_actions=conversation_state_max_recent_actions,
            )
            if conversation_state_enabled
            else None
        )

    def analyze(
        self,
        user_id: str,
        message: str,
        *,
        conversation_key: str | None = None,
        assistant_reply: str = "",
        decision_phase: str = "pre_reply",
    ) -> dict[str, Any]:
        relationship = self.memory.get_relationship(user_id)
        state = self.router.analyze(message, relationship=relationship)
        memories: list[MemorySnippet] = []
        if state.use_memory:
            memories = self.memory.relevant(
                user_id=user_id,
                message=message,
                limit=self.max_memories_per_reply,
            )

        conversation_state: dict[str, Any] | None = None
        if self.conversation_state is not None:
            resolved_key = (conversation_key or user_id).strip() or user_id
            if decision_phase == "post_reply":
                observed = self.conversation_state.observe_assistant_turn(
                    user_id,
                    resolved_key,
                    assistant_reply,
                )
            else:
                observed = self.conversation_state.observe_user_turn(
                    user_id,
                    resolved_key,
                    message,
                )
            conversation_state = observed.to_dict()

        return {
            "state": state.to_dict(),
            "memories": [memory.to_dict() for memory in memories],
            "conversation_state": conversation_state,
            "conversation_state_mode": (
                "observe_only" if self.conversation_state is not None else "disabled"
            ),
            # V4.0 deliberately does not inject conversation state into the prompt.
            "context_block": self.build_context_block(state.to_dict(), memories),
        }

    @staticmethod
    def build_context_block(state: dict[str, Any], memories: list[MemorySnippet]) -> str:
        lines = [
            "[Yogurt Social State]",
            f"场景：{state['scene']}",
            f"用户情绪：{state['mood']}",
            f"关系：{state['relationship']}",
            f"回复风格：{state['reply_style']}",
            f"建议长度：{state['reply_length']}",
            f"幽默强度：{state['humor_level']}",
            "本轮规则：",
        ]
        lines.extend(f"- {rule}" for rule in state["response_rules"])
        if memories:
            lines.append("与当前话题直接相关的记忆：")
            lines.extend(f"- {memory.text}" for memory in memories)
        else:
            lines.append("与当前话题直接相关的记忆：无")
        lines.append("只把这些信息作为辅助，不得覆盖用户最新一句话的实际含义。")
        return "\n".join(lines)
