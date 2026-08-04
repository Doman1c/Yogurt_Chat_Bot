from __future__ import annotations

import re
from dataclasses import replace
from datetime import datetime, timezone
from typing import Optional

from .conversation_state import ConversationState, ConversationStateStore


class ConversationStateRuntimeStore(ConversationStateStore):
    """Runtime reducer with completion-first and V4 phase-aware semantics."""

    @classmethod
    def _is_v41(cls, text: str) -> bool:
        return cls._contains_any(
            text,
            ("v4.1", "4.1", "responseplanner", "回复规划器", "回复规划", "planner"),
        )

    @classmethod
    def classify_dialogue_act(cls, message: str) -> str:
        text = re.sub(r"\s+", "", message.casefold())
        if cls._contains_any(
            text,
            (
                "已经完成",
                "已经实现",
                "实现完成",
                "测试通过",
                "全部通过",
                "成功合并",
                "搞定了",
                "完成了",
                "终于好了",
            ),
        ):
            return "celebrate"
        if cls._contains_any(
            text,
            ("继续", "接着", "就按照", "还是刚才", "上一步", "下一步继续"),
        ):
            return "continue"
        return super().classify_dialogue_act(message)

    @classmethod
    def topic_for_message(
        cls,
        message: str,
        previous_topic: Optional[str] = None,
    ) -> str:
        text = re.sub(r"\s+", "", message.casefold())
        if cls._is_v41(text):
            return "回复规划器"
        return super().topic_for_message(message, previous_topic)

    @classmethod
    def _thread_candidates(cls, message: str, topic: str) -> list[str]:
        text = re.sub(r"\s+", "", message.casefold())
        threads: list[str] = []
        is_v41 = cls._is_v41(text)
        if is_v41 and cls._contains_any(
            text,
            ("开始", "编写", "实现", "开发", "代码", "设计"),
        ):
            threads.append("实现 V4.1 回复规划器")
        if not is_v41:
            threads.extend(super()._thread_candidates(message, topic))
        else:
            if cls._contains_any(text, ("本地同步", "同步到本地", "拉取分支", "gitpull")):
                threads.append("本地同步 V4.1 分支")
            if cls._contains_any(text, ("开始测试", "运行测试", "测试一下", "验证")):
                threads.append("运行 V4.1 测试")
            if cls._contains_any(text, ("补全readme", "更新readme", "补全文档", "更新文档")):
                threads.append("补全 V4.1 文档")
        return threads

    @classmethod
    def _completed_thread_markers(cls, message: str) -> tuple[str, ...]:
        text = re.sub(r"\s+", "", message.casefold())
        is_v41 = cls._is_v41(text)
        completed = [] if is_v41 else list(super()._completed_thread_markers(message))
        if not cls._contains_any(
            text,
            ("已经完成", "完成了", "搞定了", "测试通过", "同步完成", "成功", "已经实现"),
        ):
            return tuple(completed)
        if is_v41 and cls._contains_any(text, ("代码", "实现", "规划器", "planner")):
            completed.append("实现 V4.1 回复规划器")
        if is_v41 and "同步" in text:
            completed.append("本地同步 V4.1 分支")
        if is_v41 and "测试" in text:
            completed.append("运行 V4.1 测试")
        if is_v41 and cls._contains_any(text, ("readme", "文档")):
            completed.append("补全 V4.1 文档")
        return tuple(dict.fromkeys(completed))

    @classmethod
    def _action_candidates(cls, message: str) -> list[str]:
        text = re.sub(r"\s+", "", message.casefold())
        is_v41 = cls._is_v41(text)
        actions = [] if is_v41 else list(super()._action_candidates(message))
        if is_v41 and cls._contains_any(text, ("测试通过", "ci通过", "全部通过")):
            actions.append("V4.1 回复规划器测试已通过")
        if is_v41 and cls._contains_any(text, ("代码写完", "实现完成", "已经实现")):
            actions.append("V4.1 回复规划器已实现")
        if is_v41 and cls._contains_any(text, ("同步完成", "已经同步", "拉取完成")):
            actions.append("V4.1 分支已同步到本地")
        return list(dict.fromkeys(actions))

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
