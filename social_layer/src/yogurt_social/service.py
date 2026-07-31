from __future__ import annotations

from pathlib import Path
from typing import Any

from .memory import MemoryStore
from .models import MemorySnippet
from .router import StyleRouter


class SocialLayerService:
    def __init__(
        self,
        db_path: str | Path,
        max_memories_per_reply: int = 3,
    ) -> None:
        self.memory = MemoryStore(db_path)
        self.router = StyleRouter()
        self.max_memories_per_reply = max_memories_per_reply

    def analyze(self, user_id: str, message: str) -> dict[str, Any]:
        relationship = self.memory.get_relationship(user_id)
        state = self.router.analyze(message, relationship=relationship)
        memories: list[MemorySnippet] = []
        if state.use_memory:
            memories = self.memory.relevant(
                user_id=user_id,
                message=message,
                limit=self.max_memories_per_reply,
            )
        return {
            "state": state.to_dict(),
            "memories": [memory.to_dict() for memory in memories],
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
