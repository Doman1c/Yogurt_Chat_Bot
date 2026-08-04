from __future__ import annotations

import json
import re
import sqlite3
from contextlib import closing
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence


_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversation_states (
    user_id TEXT NOT NULL,
    conversation_key TEXT NOT NULL,
    state_version INTEGER NOT NULL DEFAULT 1,
    current_topic TEXT,
    dialogue_act TEXT NOT NULL DEFAULT 'acknowledge',
    user_mood TEXT NOT NULL DEFAULT 'neutral',
    interaction_mode TEXT NOT NULL DEFAULT 'casual',
    open_threads_json TEXT NOT NULL DEFAULT '[]',
    recent_actions_json TEXT NOT NULL DEFAULT '[]',
    last_response_pattern TEXT,
    topic_expires_at TEXT,
    mood_expires_at TEXT,
    thread_expires_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(user_id, conversation_key)
);

CREATE TABLE IF NOT EXISTS conversation_state_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    conversation_key TEXT NOT NULL,
    state_version INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    changed_fields_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_conversation_states_user
    ON conversation_states(user_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_conversation_state_events_scope
    ON conversation_state_events(user_id, conversation_key, id DESC);
"""


@dataclass(frozen=True)
class ConversationState:
    user_id: str
    conversation_key: str
    state_version: int
    current_topic: Optional[str]
    dialogue_act: str
    user_mood: str
    interaction_mode: str
    open_threads: tuple[str, ...]
    recent_actions: tuple[str, ...]
    last_response_pattern: Optional[str]
    topic_expires_at: Optional[str]
    mood_expires_at: Optional[str]
    thread_expires_at: Optional[str]
    created_at: str
    updated_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "conversation_key": self.conversation_key,
            "state_version": self.state_version,
            "current_topic": self.current_topic,
            "dialogue_act": self.dialogue_act,
            "user_mood": self.user_mood,
            "interaction_mode": self.interaction_mode,
            "open_threads": list(self.open_threads),
            "recent_actions": list(self.recent_actions),
            "last_response_pattern": self.last_response_pattern,
            "topic_expires_at": self.topic_expires_at,
            "mood_expires_at": self.mood_expires_at,
            "thread_expires_at": self.thread_expires_at,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class ConversationStateStore:
    """V4.0 structured conversation state without raw transcript storage."""

    def __init__(
        self,
        db_path: str | Path,
        *,
        topic_ttl_minutes: int = 60,
        mood_ttl_minutes: int = 30,
        thread_ttl_minutes: int = 1440,
        max_open_threads: int = 5,
        max_recent_actions: int = 6,
        max_events_per_conversation: int = 200,
    ) -> None:
        self.db_path = Path(db_path).expanduser().resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.topic_ttl = timedelta(minutes=max(1, min(10080, topic_ttl_minutes)))
        self.mood_ttl = timedelta(minutes=max(1, min(1440, mood_ttl_minutes)))
        self.thread_ttl = timedelta(minutes=max(1, min(43200, thread_ttl_minutes)))
        self.max_open_threads = max(1, min(20, max_open_threads))
        self.max_recent_actions = max(1, min(20, max_recent_actions))
        self.max_events_per_conversation = max(20, min(2000, max_events_per_conversation))
        self.initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def initialize(self) -> None:
        with closing(self._connect()) as connection:
            connection.executescript(_SCHEMA)
            connection.commit()

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _iso(value: datetime) -> str:
        return value.astimezone(timezone.utc).isoformat()

    @staticmethod
    def _parse_time(value: Optional[str]) -> Optional[datetime]:
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    @staticmethod
    def _normalize_identifier(value: str, field: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field} must be a non-empty string")
        normalized = value.strip()
        if len(normalized) > 240:
            raise ValueError(f"{field} is too long")
        return normalized

    @staticmethod
    def _json_list(value: Any) -> tuple[str, ...]:
        try:
            items = json.loads(value or "[]")
        except (TypeError, json.JSONDecodeError):
            items = []
        if not isinstance(items, list):
            return ()
        result = [
            item.strip()[:120]
            for item in items
            if isinstance(item, str) and item.strip()
        ]
        return tuple(dict.fromkeys(result))

    def _row_to_state(self, row: sqlite3.Row) -> ConversationState:
        return ConversationState(
            user_id=str(row["user_id"]),
            conversation_key=str(row["conversation_key"]),
            state_version=int(row["state_version"]),
            current_topic=row["current_topic"],
            dialogue_act=str(row["dialogue_act"]),
            user_mood=str(row["user_mood"]),
            interaction_mode=str(row["interaction_mode"]),
            open_threads=self._json_list(row["open_threads_json"]),
            recent_actions=self._json_list(row["recent_actions_json"]),
            last_response_pattern=row["last_response_pattern"],
            topic_expires_at=row["topic_expires_at"],
            mood_expires_at=row["mood_expires_at"],
            thread_expires_at=row["thread_expires_at"],
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

    def _empty_state(
        self,
        user_id: str,
        conversation_key: str,
        now: datetime,
    ) -> ConversationState:
        stamp = self._iso(now)
        return ConversationState(
            user_id=user_id,
            conversation_key=conversation_key,
            state_version=0,
            current_topic=None,
            dialogue_act="acknowledge",
            user_mood="neutral",
            interaction_mode="casual",
            open_threads=(),
            recent_actions=(),
            last_response_pattern=None,
            topic_expires_at=None,
            mood_expires_at=None,
            thread_expires_at=None,
            created_at=stamp,
            updated_at=stamp,
        )

    def _load_raw(
        self,
        user_id: str,
        conversation_key: str,
        now: datetime,
    ) -> ConversationState:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT * FROM conversation_states WHERE user_id = ? AND conversation_key = ?",
                (user_id, conversation_key),
            ).fetchone()
        if row is not None:
            return self._row_to_state(row)
        return self._empty_state(user_id, conversation_key, now)

    def _apply_expiry(
        self,
        state: ConversationState,
        now: datetime,
    ) -> tuple[ConversationState, tuple[str, ...]]:
        expired: list[str] = []
        updates: dict[str, Any] = {}
        topic_expiry = self._parse_time(state.topic_expires_at)
        if state.current_topic and topic_expiry is not None and topic_expiry <= now:
            updates["current_topic"] = None
            updates["topic_expires_at"] = None
            expired.append("current_topic")
        mood_expiry = self._parse_time(state.mood_expires_at)
        if state.user_mood != "neutral" and mood_expiry is not None and mood_expiry <= now:
            updates["user_mood"] = "neutral"
            updates["mood_expires_at"] = None
            expired.append("user_mood")
        thread_expiry = self._parse_time(state.thread_expires_at)
        if state.open_threads and thread_expiry is not None and thread_expiry <= now:
            updates["open_threads"] = ()
            updates["thread_expires_at"] = None
            expired.append("open_threads")
        return (replace(state, **updates) if updates else state, tuple(expired))

    @staticmethod
    def _contains_any(text: str, markers: Iterable[str]) -> bool:
        return any(marker in text for marker in markers)

    @classmethod
    def classify_dialogue_act(cls, message: str) -> str:
        text = re.sub(r"\s+", "", message.casefold())
        if cls._contains_any(
            text,
            (
                "traceback", "exception", "报错", "错误", "异常", "失败", "没反应",
                "无法", "卡住", "不工作", "日志", "怎么修", "排查",
            ),
        ):
            return "troubleshoot"
        if cls._contains_any(
            text,
            (
                "开始", "设计", "计划", "实现", "编写", "开发", "下一步",
                "先做", "按照", "合并", "部署", "测试",
            ),
        ):
            return "plan"
        if cls._contains_any(
            text,
            (
                "成功", "搞定", "完成", "通过", "太好了", "好耶",
                "太开心", "终于好了", "做到了",
            ),
        ):
            return "celebrate"
        if cls._contains_any(
            text,
            ("难过", "伤心", "委屈", "焦虑", "想哭", "好累", "很累", "疼", "不舒服"),
        ):
            return "comfort"
        if cls._contains_any(text, ("继续", "接着", "就按照", "还是刚才", "上一步")):
            return "continue"
        if len(text) <= 12 and text in {
            "好", "好的", "可以", "行", "没问题", "明白", "知道了", "嗯", "对",
        }:
            return "acknowledge"
        if "?" in message or "？" in message or cls._contains_any(
            text, ("什么", "为什么", "怎么", "是否", "能不能", "可以吗", "如何")
        ):
            return "clarify"
        return "inform"

    @classmethod
    def classify_mood(cls, message: str) -> str:
        text = re.sub(r"\s+", "", message.casefold())
        if cls._contains_any(
            text,
            ("太开心", "好开心", "成功", "搞定", "完成啦", "好耶", "太好了", "兴奋"),
        ):
            return "excited"
        if cls._contains_any(
            text,
            ("烦", "崩溃", "无语", "失败", "又报错", "卡住", "没反应", "气死"),
        ):
            return "frustrated"
        if cls._contains_any(text, ("好累", "很累", "困", "疲惫", "没精神")):
            return "tired"
        if cls._contains_any(text, ("难过", "伤心", "委屈", "想哭", "失望")):
            return "sad"
        if "?" in message or "？" in message or cls._contains_any(
            text, ("想知道", "好奇", "为什么", "怎么")
        ):
            return "curious"
        return "neutral"

    @classmethod
    def classify_interaction_mode(
        cls,
        message: str,
        dialogue_act: str,
        mood: str,
    ) -> str:
        text = re.sub(r"\s+", "", message.casefold())
        if mood in {"sad", "tired"}:
            return "supportive"
        if dialogue_act == "troubleshoot" or cls._contains_any(
            text,
            ("代码", "脚本", "接口", "配置", "日志", "git", "python", "node", "openclaw"),
        ):
            return "technical"
        if cls._contains_any(
            text,
            ("我们", "一起", "开始", "按照", "继续", "帮我", "下一步", "设计", "开发"),
        ):
            return "collaborative"
        return "casual"

    @classmethod
    def topic_for_message(
        cls,
        message: str,
        previous_topic: Optional[str] = None,
    ) -> str:
        text = re.sub(r"\s+", "", message.casefold())
        rules: Sequence[tuple[str, tuple[str, ...]]] = (
            ("Yogurt V4 开发", ("v4", "conversationstate", "连续对话状态", "状态引擎")),
            ("Yogurt V3 维护", ("v3",)),
            ("回复规划器", ("responseplanner", "回复规划", "planner")),
            ("记忆系统", ("自动记忆", "长期记忆", "memory", "回忆")),
            ("统一发送编排", ("outboundcoordinator", "发送编排", "发送顺序")),
            ("表情系统", ("表情包", "sticker")),
            ("多气泡回复", ("多气泡", "气泡回复")),
            ("OpenClaw 集成", ("openclaw", "gateway", "hook")),
            ("微信机器人", ("微信", "weixin")),
            ("本地模型部署", ("ollama", "qwen", "本地模型")),
            ("Minecraft 集成", ("minecraft", "mc服务器", "游戏角色")),
        )
        for topic, markers in rules:
            if cls._contains_any(text, markers):
                return topic
        if cls._contains_any(
            text,
            ("报错", "错误", "失败", "异常", "日志", "卡住", "没反应", "排查"),
        ):
            return "技术排查"
        if previous_topic and (
            len(text) <= 30
            or cls._contains_any(text, ("继续", "接着", "按照", "下一步", "开始", "测试"))
        ):
            return previous_topic
        return "日常对话"

    @classmethod
    def _thread_candidates(cls, message: str, topic: str) -> list[str]:
        text = re.sub(r"\s+", "", message.casefold())
        threads: list[str] = []
        if "v4" in text and cls._contains_any(text, ("编写", "实现", "开发", "代码", "开始")):
            threads.append("实现 V4.0 会话状态")
        if cls._contains_any(text, ("本地同步", "同步到本地", "拉取分支", "gitpull")):
            threads.append("本地同步 V4 分支")
        if cls._contains_any(text, ("开始测试", "运行测试", "测试一下", "验证")):
            threads.append("运行 V4.0 测试")
        if cls._contains_any(text, ("补全readme", "更新readme", "补全文档", "更新文档")):
            threads.append("补全 V4 文档")
        if cls._contains_any(text, ("合并到main", "合并main", "merge到main")):
            threads.append("合并 V4 到 main")
        if not threads and topic == "技术排查":
            threads.append("完成当前技术排查")
        return threads

    @classmethod
    def _completed_thread_markers(cls, message: str) -> tuple[str, ...]:
        text = re.sub(r"\s+", "", message.casefold())
        if not cls._contains_any(
            text,
            ("已经完成", "完成了", "搞定了", "测试通过", "同步完成", "合并完成", "成功"),
        ):
            return ()
        completed: list[str] = []
        if cls._contains_any(text, ("代码", "实现", "v4.0")):
            completed.append("实现 V4.0 会话状态")
        if "同步" in text:
            completed.append("本地同步 V4 分支")
        if "测试" in text:
            completed.append("运行 V4.0 测试")
        if cls._contains_any(text, ("readme", "文档")):
            completed.append("补全 V4 文档")
        if cls._contains_any(text, ("合并", "main")):
            completed.append("合并 V4 到 main")
        return tuple(completed)

    @classmethod
    def _action_candidates(cls, message: str) -> list[str]:
        text = re.sub(r"\s+", "", message.casefold())
        actions: list[str] = []
        if cls._contains_any(text, ("测试通过", "ci通过", "全部通过")):
            actions.append("V4.0 测试已通过")
        if cls._contains_any(text, ("同步完成", "已经同步", "拉取完成")):
            actions.append("V4 分支已同步到本地")
        if cls._contains_any(text, ("合并完成", "已合并到main", "成功合并")):
            actions.append("V4 已合并到 main")
        if cls._contains_any(text, ("代码写完", "实现完成", "已经实现")):
            actions.append("V4.0 状态引擎已实现")
        return actions

    @staticmethod
    def response_pattern(assistant_reply: str) -> str:
        text = assistant_reply.strip()
        if not text:
            return "empty"
        if "```" in text:
            return "technical_code"
        if len(text) >= 700:
            return "technical_long"
        if len(text) >= 260:
            return "explanatory_medium"
        if text.count("\n\n") >= 2:
            return "multi_paragraph"
        if len(text) <= 80:
            return "concise"
        return "standard"

    @staticmethod
    def _merge_bounded(
        existing: Sequence[str],
        additions: Sequence[str],
        limit: int,
    ) -> tuple[str, ...]:
        ordered = [item for item in existing if item]
        for item in additions:
            normalized = item.strip()[:120]
            if not normalized:
                continue
            if normalized in ordered:
                ordered.remove(normalized)
            ordered.append(normalized)
        return tuple(ordered[-limit:])

    @staticmethod
    def _changed_fields(
        before: ConversationState,
        after: ConversationState,
    ) -> dict[str, Any]:
        fields = (
            "current_topic", "dialogue_act", "user_mood", "interaction_mode",
            "open_threads", "recent_actions", "last_response_pattern",
            "topic_expires_at", "mood_expires_at", "thread_expires_at",
        )
        changed: dict[str, Any] = {}
        for field in fields:
            old = getattr(before, field)
            new = getattr(after, field)
            if old != new:
                changed[field] = list(new) if isinstance(new, tuple) else new
        return changed

    def _write(
        self,
        before: ConversationState,
        after: ConversationState,
        event_type: str,
        changed_fields: dict[str, Any],
    ) -> ConversationState:
        del before
        with closing(self._connect()) as connection:
            connection.execute(
                """
                INSERT INTO conversation_states(
                    user_id, conversation_key, state_version, current_topic,
                    dialogue_act, user_mood, interaction_mode, open_threads_json,
                    recent_actions_json, last_response_pattern, topic_expires_at,
                    mood_expires_at, thread_expires_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, conversation_key) DO UPDATE SET
                    state_version = excluded.state_version,
                    current_topic = excluded.current_topic,
                    dialogue_act = excluded.dialogue_act,
                    user_mood = excluded.user_mood,
                    interaction_mode = excluded.interaction_mode,
                    open_threads_json = excluded.open_threads_json,
                    recent_actions_json = excluded.recent_actions_json,
                    last_response_pattern = excluded.last_response_pattern,
                    topic_expires_at = excluded.topic_expires_at,
                    mood_expires_at = excluded.mood_expires_at,
                    thread_expires_at = excluded.thread_expires_at,
                    updated_at = excluded.updated_at
                """,
                (
                    after.user_id, after.conversation_key, after.state_version,
                    after.current_topic, after.dialogue_act, after.user_mood,
                    after.interaction_mode,
                    json.dumps(list(after.open_threads), ensure_ascii=False),
                    json.dumps(list(after.recent_actions), ensure_ascii=False),
                    after.last_response_pattern, after.topic_expires_at,
                    after.mood_expires_at, after.thread_expires_at,
                    after.created_at, after.updated_at,
                ),
            )
            connection.execute(
                """
                INSERT INTO conversation_state_events(
                    user_id, conversation_key, state_version, event_type,
                    changed_fields_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    after.user_id, after.conversation_key, after.state_version,
                    event_type,
                    json.dumps(changed_fields, ensure_ascii=False, sort_keys=True),
                    after.updated_at,
                ),
            )
            connection.execute(
                """
                DELETE FROM conversation_state_events
                WHERE id IN (
                    SELECT id FROM conversation_state_events
                    WHERE user_id = ? AND conversation_key = ?
                    ORDER BY id DESC LIMIT -1 OFFSET ?
                )
                """,
                (after.user_id, after.conversation_key, self.max_events_per_conversation),
            )
            connection.commit()
        return after

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
        open_threads = self._merge_bounded(
            remaining_threads,
            self._thread_candidates(message, topic),
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

    def observe_assistant_turn(
        self,
        user_id: str,
        conversation_key: str,
        assistant_reply: str,
        *,
        now: Optional[datetime] = None,
    ) -> ConversationState:
        user_id = self._normalize_identifier(user_id, "user_id")
        conversation_key = self._normalize_identifier(conversation_key, "conversation_key")
        if not isinstance(assistant_reply, str):
            raise TypeError("assistant_reply must be a string")
        current_time = (now or self._now()).astimezone(timezone.utc)
        raw_before = self._load_raw(user_id, conversation_key, current_time)
        before, expired = self._apply_expiry(raw_before, current_time)
        after = replace(
            before,
            state_version=raw_before.state_version + 1,
            recent_actions=self._merge_bounded(
                before.recent_actions,
                self._action_candidates(assistant_reply),
                self.max_recent_actions,
            ),
            last_response_pattern=self.response_pattern(assistant_reply),
            updated_at=self._iso(current_time),
        )
        changed = self._changed_fields(raw_before, after)
        if expired:
            changed["expired_fields"] = list(expired)
        return self._write(raw_before, after, "assistant_turn", changed)

    def get(
        self,
        user_id: str,
        conversation_key: str,
        *,
        now: Optional[datetime] = None,
    ) -> Optional[ConversationState]:
        user_id = self._normalize_identifier(user_id, "user_id")
        conversation_key = self._normalize_identifier(conversation_key, "conversation_key")
        current_time = (now or self._now()).astimezone(timezone.utc)
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT * FROM conversation_states WHERE user_id = ? AND conversation_key = ?",
                (user_id, conversation_key),
            ).fetchone()
        if row is None:
            return None
        state, _ = self._apply_expiry(self._row_to_state(row), current_time)
        return state

    def list_states(
        self,
        user_id: Optional[str] = None,
        *,
        limit: int = 100,
        now: Optional[datetime] = None,
    ) -> list[dict[str, Any]]:
        current_time = (now or self._now()).astimezone(timezone.utc)
        query = "SELECT * FROM conversation_states"
        params: list[Any] = []
        if user_id is not None:
            query += " WHERE user_id = ?"
            params.append(self._normalize_identifier(user_id, "user_id"))
        query += " ORDER BY updated_at DESC LIMIT ?"
        params.append(max(1, min(500, limit)))
        with closing(self._connect()) as connection:
            rows = connection.execute(query, tuple(params)).fetchall()
        items: list[dict[str, Any]] = []
        for row in rows:
            raw = self._row_to_state(row)
            active, expired = self._apply_expiry(raw, current_time)
            payload = active.to_dict()
            payload["expired_fields"] = list(expired)
            items.append(payload)
        return items

    def list_events(
        self,
        user_id: str,
        conversation_key: str,
        *,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        user_id = self._normalize_identifier(user_id, "user_id")
        conversation_key = self._normalize_identifier(conversation_key, "conversation_key")
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT id, state_version, event_type, changed_fields_json, created_at
                FROM conversation_state_events
                WHERE user_id = ? AND conversation_key = ?
                ORDER BY id DESC LIMIT ?
                """,
                (user_id, conversation_key, max(1, min(500, limit))),
            ).fetchall()
        items: list[dict[str, Any]] = []
        for row in rows:
            try:
                changed_fields = json.loads(row["changed_fields_json"])
            except (TypeError, json.JSONDecodeError):
                changed_fields = {}
            items.append(
                {
                    "id": int(row["id"]),
                    "state_version": int(row["state_version"]),
                    "event_type": str(row["event_type"]),
                    "changed_fields": changed_fields,
                    "created_at": str(row["created_at"]),
                }
            )
        return items
