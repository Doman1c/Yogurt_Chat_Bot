from __future__ import annotations

import re
from dataclasses import replace
from datetime import datetime, timezone
from typing import Optional

from .conversation_state import ConversationState, ConversationStateStore


class ConversationStateRuntimeStore(ConversationStateStore):
    """Runtime reducer with completion-first and V4 phase-aware semantics."""

    _V40_SUPERSEDED_THREADS = frozenset(
        {
            "实现 V4.0 会话状态",
            "运行 V4.0 测试",
        }
    )
    _V41_SUPERSEDED_THREADS = frozenset(
        {
            "实现 V4.1 回复规划器",
            "本地同步 V4.1 分支",
            "运行 V4.1 测试",
        }
    )
    _V42_SUPERSEDED_THREADS = frozenset(
        {
            "实现 V4.2 记忆召回门控",
            "验证 V4.2 影子召回决策",
            "本地同步 V4.2 分支",
            "运行 V4.2 测试",
        }
    )

    @classmethod
    def _is_v43(cls, text: str) -> bool:
        return cls._contains_any(
            text,
            (
                "v4.3",
                "4.3",
                "unifiedoutbound",
                "outboundcoordinator",
                "统一发送协调",
                "统一发送",
                "发送协调器",
                "outboundplan",
            ),
        )

    @classmethod
    def _is_v42(cls, text: str) -> bool:
        return cls._contains_any(
            text,
            (
                "v4.2",
                "4.2",
                "memoryrecallgate",
                "memoryrecall",
                "记忆召回门控",
                "记忆召回",
                "召回门控",
                "recallgate",
            ),
        )

    @classmethod
    def _is_v41(cls, text: str) -> bool:
        return cls._contains_any(
            text,
            ("v4.1", "4.1", "responseplanner", "回复规划器", "回复规划", "planner"),
        )

    @classmethod
    def _test_passed(cls, text: str) -> bool:
        return cls._contains_any(
            text,
            (
                "测试通过",
                "测试也通过",
                "测试已经通过",
                "测试都通过",
                "测试全部通过",
                "测试均通过",
                "测试顺利通过",
                "全部测试通过",
                "ci通过",
                "ci也通过",
                "ci已经通过",
            ),
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
                "全部通过",
                "成功合并",
                "搞定了",
                "完成了",
                "终于好了",
            ),
        ) or cls._test_passed(text):
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
        if cls._is_v43(text):
            return "统一发送协调"
        if cls._is_v42(text):
            return "记忆召回门控"
        if cls._is_v41(text):
            return "回复规划器"
        return super().topic_for_message(message, previous_topic)

    @classmethod
    def _thread_candidates(cls, message: str, topic: str) -> list[str]:
        text = re.sub(r"\s+", "", message.casefold())
        threads: list[str] = []
        is_v43 = cls._is_v43(text)
        is_v42 = cls._is_v42(text) and not is_v43
        is_v41 = cls._is_v41(text) and not is_v42 and not is_v43

        if is_v43:
            if cls._contains_any(
                text,
                ("开始", "编写", "实现", "开发", "代码", "设计"),
            ):
                threads.append("实现 V4.3 统一发送协调器")
            if cls._contains_any(text, ("影子模式", "shadow", "观察模式")):
                threads.append("验证 V4.3 影子发送计划")
            if cls._contains_any(text, ("本地同步", "同步到本地", "拉取分支", "gitpull")):
                threads.append("本地同步 V4.3 分支")
            if cls._contains_any(text, ("开始测试", "运行测试", "测试一下", "验证")):
                threads.append("运行 V4.3 测试")
            if cls._contains_any(text, ("补全readme", "更新readme", "补全文档", "更新文档")):
                threads.append("补全 V4.3 文档")
            return threads

        if is_v42:
            if cls._contains_any(
                text,
                ("开始", "编写", "实现", "开发", "代码", "设计"),
            ):
                threads.append("实现 V4.2 记忆召回门控")
            if cls._contains_any(text, ("影子模式", "shadow", "观察模式")):
                threads.append("验证 V4.2 影子召回决策")
            if cls._contains_any(text, ("本地同步", "同步到本地", "拉取分支", "gitpull")):
                threads.append("本地同步 V4.2 分支")
            if cls._contains_any(text, ("开始测试", "运行测试", "测试一下", "验证")):
                threads.append("运行 V4.2 测试")
            if cls._contains_any(text, ("补全readme", "更新readme", "补全文档", "更新文档")):
                threads.append("补全 V4.2 文档")
            return threads

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
        is_v43 = cls._is_v43(text)
        is_v42 = cls._is_v42(text) and not is_v43
        is_v41 = cls._is_v41(text) and not is_v42 and not is_v43
        completed = [] if (is_v41 or is_v42 or is_v43) else list(
            super()._completed_thread_markers(message)
        )
        has_completion = cls._contains_any(
            text,
            ("已经完成", "完成了", "搞定了", "同步完成", "成功", "已经实现"),
        ) or cls._test_passed(text)
        if not has_completion:
            return tuple(completed)

        if is_v43:
            if cls._contains_any(text, ("代码", "实现", "协调", "outbound", "发送计划")):
                completed.append("实现 V4.3 统一发送协调器")
            if cls._contains_any(text, ("影子模式", "shadow", "观察模式")):
                completed.append("验证 V4.3 影子发送计划")
            if "同步" in text:
                completed.append("本地同步 V4.3 分支")
            if "测试" in text and cls._test_passed(text):
                completed.append("运行 V4.3 测试")
            if cls._contains_any(text, ("readme", "文档")):
                completed.append("补全 V4.3 文档")
            return tuple(dict.fromkeys(completed))

        if is_v42:
            if cls._contains_any(text, ("代码", "实现", "门控", "recall", "召回")):
                completed.append("实现 V4.2 记忆召回门控")
            if cls._contains_any(text, ("影子模式", "shadow", "观察模式")):
                completed.append("验证 V4.2 影子召回决策")
            if "同步" in text:
                completed.append("本地同步 V4.2 分支")
            if "测试" in text and cls._test_passed(text):
                completed.append("运行 V4.2 测试")
            if cls._contains_any(text, ("readme", "文档")):
                completed.append("补全 V4.2 文档")
            return tuple(dict.fromkeys(completed))

        if is_v41 and cls._contains_any(text, ("代码", "实现", "规划器", "planner")):
            completed.append("实现 V4.1 回复规划器")
        if is_v41 and "同步" in text:
            completed.append("本地同步 V4.1 分支")
        if is_v41 and "测试" in text and cls._test_passed(text):
            completed.append("运行 V4.1 测试")
        if is_v41 and cls._contains_any(text, ("readme", "文档")):
            completed.append("补全 V4.1 文档")
        return tuple(dict.fromkeys(completed))

    @classmethod
    def _action_candidates(cls, message: str) -> list[str]:
        text = re.sub(r"\s+", "", message.casefold())
        is_v43 = cls._is_v43(text)
        is_v42 = cls._is_v42(text) and not is_v43
        is_v41 = cls._is_v41(text) and not is_v42 and not is_v43
        actions = [] if (is_v41 or is_v42 or is_v43) else list(
            super()._action_candidates(message)
        )

        if is_v43:
            if cls._test_passed(text):
                actions.append("V4.3 统一发送协调器测试已通过")
            if cls._contains_any(text, ("代码写完", "实现完成", "已经实现")):
                actions.append("V4.3 统一发送协调器已实现")
            if cls._contains_any(text, ("影子模式通过", "shadow通过", "观察模式通过")):
                actions.append("V4.3 影子发送计划已验证")
            if cls._contains_any(text, ("同步完成", "已经同步", "拉取完成")):
                actions.append("V4.3 分支已同步到本地")
            return list(dict.fromkeys(actions))

        if is_v42:
            if cls._test_passed(text):
                actions.append("V4.2 记忆召回门控测试已通过")
            if cls._contains_any(text, ("代码写完", "实现完成", "已经实现")):
                actions.append("V4.2 记忆召回门控已实现")
            if cls._contains_any(text, ("影子模式通过", "shadow通过", "观察模式通过")):
                actions.append("V4.2 影子召回决策已验证")
            if cls._contains_any(text, ("同步完成", "已经同步", "拉取完成")):
                actions.append("V4.2 分支已同步到本地")
            return list(dict.fromkeys(actions))

        if is_v41 and cls._test_passed(text):
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
        normalized_text = re.sub(r"\s+", "", message.casefold())
        v43_phase_active = self._is_v43(normalized_text) or topic == "统一发送协调"
        v42_phase_active = (
            self._is_v42(normalized_text) or topic == "记忆召回门控"
        ) and not v43_phase_active
        v41_phase_active = (
            self._is_v41(normalized_text) or topic == "回复规划器"
        ) and not v42_phase_active and not v43_phase_active
        superseded_threads = set()
        if v41_phase_active or v42_phase_active or v43_phase_active:
            superseded_threads.update(self._V40_SUPERSEDED_THREADS)
        if v42_phase_active or v43_phase_active:
            superseded_threads.update(self._V41_SUPERSEDED_THREADS)
        if v43_phase_active:
            superseded_threads.update(self._V42_SUPERSEDED_THREADS)
        remaining_threads = tuple(
            item
            for item in before.open_threads
            if item not in completed_threads and item not in superseded_threads
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
