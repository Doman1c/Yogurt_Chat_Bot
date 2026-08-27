from __future__ import annotations

import json
import re
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

from .conversation_state import ConversationState
from .memory import MemoryStore


_SCHEMA = """
CREATE TABLE IF NOT EXISTS memory_recall_conversations (
    user_id TEXT NOT NULL,
    conversation_key TEXT NOT NULL,
    turn_index INTEGER NOT NULL DEFAULT 0,
    last_allowed_turn INTEGER,
    last_memory_kind TEXT,
    last_memory_id INTEGER,
    last_decision TEXT NOT NULL DEFAULT 'none',
    updated_at TEXT NOT NULL,
    PRIMARY KEY(user_id, conversation_key)
);

CREATE TABLE IF NOT EXISTS memory_recall_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    conversation_key TEXT NOT NULL,
    state_version INTEGER NOT NULL,
    turn_index INTEGER NOT NULL,
    decision TEXT NOT NULL,
    candidate_layer TEXT,
    candidate_kind TEXT,
    candidate_id INTEGER,
    score REAL,
    threshold REAL NOT NULL,
    explicit_request INTEGER NOT NULL DEFAULT 0,
    reasons_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_memory_recall_events_scope
ON memory_recall_events(user_id, conversation_key, id DESC);
"""


_EXPLICIT_RECALL_MARKERS = (
    "还记得",
    "你记得",
    "记得我",
    "上次",
    "之前说过",
    "之前提过",
    "我之前",
    "以前提过",
    "根据我的",
    "按我之前",
)

_HIGH_RISK_MARKERS = (
    "签证",
    "行政审理",
    "法律",
    "合同",
    "医院",
    "手术",
    "药物",
    "剂量",
    "拔牙",
    "账号被盗",
    "密码",
    "验证码",
    "token",
    "api key",
    "密钥",
    "银行卡",
    "信用卡",
)

_LOW_INFORMATION_ACTS = {"acknowledge"}
_NON_INTERRUPTION_MOODS = {"sad", "tired", "frustrated"}


@dataclass(frozen=True)
class RecallCandidate:
    layer: str
    kind: str
    source_id: int
    memory_key: str
    text: str
    score: float
    lexical_score: float
    continuity_score: float
    quality_score: float

    def to_dict(self, *, include_text: bool = False) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "layer": self.layer,
            "kind": self.kind,
            "source_id": self.source_id,
            "memory_key": self.memory_key,
            "score": round(self.score, 4),
            "lexical_score": round(self.lexical_score, 4),
            "continuity_score": round(self.continuity_score, 4),
            "quality_score": round(self.quality_score, 4),
        }
        if include_text:
            payload["text"] = self.text
        return payload


@dataclass(frozen=True)
class RecallDecision:
    mode: str
    decision: str
    threshold: float
    explicit_request: bool
    turn_index: int
    state_version: int
    cooldown_remaining: int
    same_memory_cooldown_remaining: int
    reasons: tuple[str, ...]
    candidate: Optional[RecallCandidate]

    def to_dict(self, *, include_text: bool = False) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "decision": self.decision,
            "threshold": round(self.threshold, 4),
            "explicit_request": self.explicit_request,
            "turn_index": self.turn_index,
            "state_version": self.state_version,
            "cooldown_remaining": self.cooldown_remaining,
            "same_memory_cooldown_remaining": self.same_memory_cooldown_remaining,
            "reasons": list(self.reasons),
            "candidate": (
                self.candidate.to_dict(include_text=include_text)
                if self.candidate is not None
                else None
            ),
        }


class MemoryRecallGate:
    """Deterministic V4.2 shadow gate for stable and episodic memories.

    The gate reads promoted V1/V3 memories and V4 temporary conversation state.
    It stores only candidate identity, scores and decision reasons. The current
    user message and memory text are never copied into recall audit tables.
    """

    def __init__(
        self,
        db_path: str | Path,
        *,
        min_score: float = 0.48,
        cooldown_turns: int = 8,
        same_memory_cooldown_turns: int = 20,
        max_candidates: int = 5,
        max_events_per_conversation: int = 300,
    ) -> None:
        self.db_path = Path(db_path).expanduser().resolve()
        self.base = MemoryStore(self.db_path)
        self.min_score = min(0.95, max(0.2, float(min_score)))
        self.cooldown_turns = max(0, min(100, int(cooldown_turns)))
        self.same_memory_cooldown_turns = max(
            self.cooldown_turns,
            min(300, int(same_memory_cooldown_turns)),
        )
        self.max_candidates = max(1, min(20, int(max_candidates)))
        self.max_events_per_conversation = max(
            20,
            min(2000, int(max_events_per_conversation)),
        )
        with closing(self._connect()) as connection:
            connection.executescript(_SCHEMA)
            connection.commit()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA busy_timeout = 3000")
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _normalize_identifier(value: str, field: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field} must be a non-empty string")
        normalized = value.strip()
        if len(normalized) > 240:
            raise ValueError(f"{field} is too long")
        return normalized

    @staticmethod
    def _compact(text: str) -> str:
        return re.sub(r"\s+", "", text.casefold())

    @classmethod
    def _contains_any(cls, text: str, markers: Iterable[str]) -> bool:
        return any(marker in text for marker in markers)

    @classmethod
    def _explicit_request(cls, message: str) -> bool:
        return cls._contains_any(cls._compact(message), _EXPLICIT_RECALL_MARKERS)

    @classmethod
    def _high_risk_topic(cls, message: str) -> bool:
        return cls._contains_any(cls._compact(message), _HIGH_RISK_MARKERS)

    @staticmethod
    def _quality(value: float) -> float:
        return min(1.0, max(0.0, float(value)))

    @classmethod
    def _score(
        cls,
        *,
        message: str,
        continuity_texts: Sequence[str],
        candidate_text: str,
        quality: float,
        explicit_request: bool,
    ) -> tuple[float, float, float, float]:
        message_tokens = MemoryStore._tokens(message)
        lexical = MemoryStore._relevance(message_tokens, candidate_text)
        continuity = 0.0
        for text in continuity_texts:
            if not text:
                continue
            continuity = max(
                continuity,
                MemoryStore._relevance(MemoryStore._tokens(text), candidate_text),
            )
        quality_score = cls._quality(quality)
        score = lexical * 0.72 + continuity * 0.18 + quality_score * 0.10
        if explicit_request and lexical > 0:
            score += 0.06
        return (
            min(1.0, score),
            lexical,
            continuity,
            quality_score,
        )

    def _candidates(
        self,
        user_id: str,
        message: str,
        state: ConversationState,
        explicit_request: bool,
    ) -> list[RecallCandidate]:
        continuity_texts = [state.current_topic or "", *state.open_threads]
        with closing(self._connect()) as connection:
            facts = connection.execute(
                """
                SELECT id, fact_key, fact_value, confidence, source
                FROM facts
                WHERE user_id = ? AND active = 1
                """,
                (user_id,),
            ).fetchall()
            preferences = connection.execute(
                """
                SELECT id, category, value, polarity, confidence, source
                FROM preferences
                WHERE user_id = ? AND active = 1
                """,
                (user_id,),
            ).fetchall()
            episodes = connection.execute(
                """
                SELECT id, summary, importance, source
                FROM episodes
                WHERE user_id = ? AND active = 1
                ORDER BY id DESC LIMIT 120
                """,
                (user_id,),
            ).fetchall()

        candidates: list[RecallCandidate] = []
        for row in facts:
            text = f"{row['fact_key']}：{row['fact_value']}"
            score, lexical, continuity, quality = self._score(
                message=message,
                continuity_texts=continuity_texts,
                candidate_text=text,
                quality=float(row["confidence"]),
                explicit_request=explicit_request,
            )
            candidates.append(
                RecallCandidate(
                    "stable",
                    "fact",
                    int(row["id"]),
                    str(row["fact_key"]),
                    text,
                    score,
                    lexical,
                    continuity,
                    quality,
                )
            )

        for row in preferences:
            verb = "喜欢" if int(row["polarity"]) > 0 else "不喜欢"
            text = f"{row['category']}：用户{verb}{row['value']}"
            score, lexical, continuity, quality = self._score(
                message=message,
                continuity_texts=continuity_texts,
                candidate_text=text,
                quality=float(row["confidence"]),
                explicit_request=explicit_request,
            )
            candidates.append(
                RecallCandidate(
                    "stable",
                    "preference",
                    int(row["id"]),
                    str(row["category"]),
                    text,
                    score,
                    lexical,
                    continuity,
                    quality,
                )
            )

        for row in episodes:
            text = str(row["summary"])
            score, lexical, continuity, quality = self._score(
                message=message,
                continuity_texts=continuity_texts,
                candidate_text=text,
                quality=float(row["importance"]),
                explicit_request=explicit_request,
            )
            candidates.append(
                RecallCandidate(
                    "episodic",
                    "episode",
                    int(row["id"]),
                    "milestone",
                    text,
                    score,
                    lexical,
                    continuity,
                    quality,
                )
            )

        candidates.sort(
            key=lambda item: (
                item.score,
                item.lexical_score,
                item.quality_score,
                1 if item.layer == "stable" else 0,
                item.source_id,
            ),
            reverse=True,
        )
        return candidates[: self.max_candidates]

    def _conversation_row(
        self,
        connection: sqlite3.Connection,
        user_id: str,
        conversation_key: str,
    ) -> Optional[sqlite3.Row]:
        return connection.execute(
            """
            SELECT * FROM memory_recall_conversations
            WHERE user_id = ? AND conversation_key = ?
            """,
            (user_id, conversation_key),
        ).fetchone()

    def _persist(
        self,
        *,
        user_id: str,
        conversation_key: str,
        state_version: int,
        turn_index: int,
        decision: str,
        threshold: float,
        explicit_request: bool,
        reasons: Sequence[str],
        candidate: Optional[RecallCandidate],
        previous: Optional[sqlite3.Row],
    ) -> None:
        now = self._now()
        allowed = decision == "allow" and candidate is not None
        previous_allowed_turn = (
            int(previous["last_allowed_turn"])
            if previous is not None and previous["last_allowed_turn"] is not None
            else None
        )
        previous_kind = (
            str(previous["last_memory_kind"])
            if previous is not None and previous["last_memory_kind"] is not None
            else None
        )
        previous_id = (
            int(previous["last_memory_id"])
            if previous is not None and previous["last_memory_id"] is not None
            else None
        )
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO memory_recall_conversations(
                    user_id, conversation_key, turn_index, last_allowed_turn,
                    last_memory_kind, last_memory_id, last_decision, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, conversation_key) DO UPDATE SET
                    turn_index = excluded.turn_index,
                    last_allowed_turn = excluded.last_allowed_turn,
                    last_memory_kind = excluded.last_memory_kind,
                    last_memory_id = excluded.last_memory_id,
                    last_decision = excluded.last_decision,
                    updated_at = excluded.updated_at
                """,
                (
                    user_id,
                    conversation_key,
                    turn_index,
                    turn_index if allowed else previous_allowed_turn,
                    candidate.kind if allowed and candidate is not None else previous_kind,
                    candidate.source_id if allowed and candidate is not None else previous_id,
                    decision,
                    now,
                ),
            )
            connection.execute(
                """
                INSERT INTO memory_recall_events(
                    user_id, conversation_key, state_version, turn_index,
                    decision, candidate_layer, candidate_kind, candidate_id,
                    score, threshold, explicit_request, reasons_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    conversation_key,
                    state_version,
                    turn_index,
                    decision,
                    candidate.layer if candidate is not None else None,
                    candidate.kind if candidate is not None else None,
                    candidate.source_id if candidate is not None else None,
                    candidate.score if candidate is not None else None,
                    threshold,
                    1 if explicit_request else 0,
                    json.dumps(list(reasons), ensure_ascii=False),
                    now,
                ),
            )
            connection.execute(
                """
                DELETE FROM memory_recall_events
                WHERE id IN (
                    SELECT id FROM memory_recall_events
                    WHERE user_id = ? AND conversation_key = ?
                    ORDER BY id DESC LIMIT -1 OFFSET ?
                )
                """,
                (
                    user_id,
                    conversation_key,
                    self.max_events_per_conversation,
                ),
            )
            connection.commit()

    def evaluate(
        self,
        *,
        user_id: str,
        conversation_key: str,
        message: str,
        state: ConversationState,
        candidates_override: Sequence[RecallCandidate] | None = None,
    ) -> RecallDecision:
        user_id = self._normalize_identifier(user_id, "user_id")
        conversation_key = self._normalize_identifier(
            conversation_key,
            "conversation_key",
        )
        if not isinstance(message, str):
            raise TypeError("message must be a string")

        explicit_request = self._explicit_request(message)
        threshold = self.min_score - (0.12 if explicit_request else 0.0)
        if self._high_risk_topic(message):
            threshold += 0.10
        threshold = min(0.95, max(0.2, threshold))

        with closing(self._connect()) as connection:
            previous = self._conversation_row(connection, user_id, conversation_key)
        turn_index = (int(previous["turn_index"]) if previous is not None else 0) + 1
        last_allowed_turn = (
            int(previous["last_allowed_turn"])
            if previous is not None and previous["last_allowed_turn"] is not None
            else None
        )
        cooldown_remaining = 0
        if last_allowed_turn is not None:
            cooldown_remaining = max(
                0,
                self.cooldown_turns - (turn_index - last_allowed_turn) + 1,
            )

        candidates = (
            list(candidates_override)[: self.max_candidates]
            if candidates_override is not None
            else self._candidates(user_id, message, state, explicit_request)
        )
        candidate = candidates[0] if candidates else None
        same_memory_remaining = 0
        if candidate is not None and previous is not None:
            previous_kind = previous["last_memory_kind"]
            previous_id = previous["last_memory_id"]
            if (
                previous_kind == candidate.kind
                and previous_id is not None
                and int(previous_id) == candidate.source_id
                and last_allowed_turn is not None
            ):
                same_memory_remaining = max(
                    0,
                    self.same_memory_cooldown_turns
                    - (turn_index - last_allowed_turn)
                    + 1,
                )

        reasons: list[str] = []
        decision = "allow"
        if candidate is None:
            decision = "none"
            reasons.append("no_candidate")
        else:
            if candidate.score < threshold:
                decision = "blocked"
                reasons.append("relevance_below_threshold")
            if state.dialogue_act in _LOW_INFORMATION_ACTS and not explicit_request:
                decision = "blocked"
                reasons.append("low_information_turn")
            if state.user_mood in _NON_INTERRUPTION_MOODS and not explicit_request:
                decision = "blocked"
                reasons.append("emotion_non_interruption")
            if state.dialogue_act in {"celebrate", "comfort"} and not explicit_request:
                decision = "blocked"
                reasons.append("moment_non_interruption")
            if self._high_risk_topic(message) and not explicit_request:
                decision = "blocked"
                reasons.append("high_risk_topic")
            if cooldown_remaining > 0 and not explicit_request:
                decision = "blocked"
                reasons.append("conversation_cooldown")
            if same_memory_remaining > 0 and not explicit_request:
                decision = "blocked"
                reasons.append("same_memory_cooldown")
            if candidate.quality_score < 0.55:
                decision = "blocked"
                reasons.append("low_memory_quality")

        if explicit_request:
            reasons.append("explicit_recall_request")
        if candidate is not None:
            reasons.append(f"candidate_layer:{candidate.layer}")
            reasons.append(f"candidate_kind:{candidate.kind}")
        if decision == "allow":
            reasons.append("gate_allow")
        elif decision == "none":
            reasons.append("gate_noop")
        else:
            reasons.append("gate_block")
        reasons = list(dict.fromkeys(reasons))[:12]

        self._persist(
            user_id=user_id,
            conversation_key=conversation_key,
            state_version=state.state_version,
            turn_index=turn_index,
            decision=decision,
            threshold=threshold,
            explicit_request=explicit_request,
            reasons=reasons,
            candidate=candidate,
            previous=previous,
        )
        return RecallDecision(
            mode="shadow_only",
            decision=decision,
            threshold=threshold,
            explicit_request=explicit_request,
            turn_index=turn_index,
            state_version=state.state_version,
            cooldown_remaining=cooldown_remaining,
            same_memory_cooldown_remaining=same_memory_remaining,
            reasons=tuple(reasons),
            candidate=candidate,
        )

    def events(
        self,
        *,
        user_id: str,
        conversation_key: Optional[str] = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        user_id = self._normalize_identifier(user_id, "user_id")
        bounded_limit = max(1, min(500, int(limit)))
        query = """
            SELECT id, user_id, conversation_key, state_version, turn_index,
                   decision, candidate_layer, candidate_kind, candidate_id,
                   score, threshold, explicit_request, reasons_json, created_at
            FROM memory_recall_events
            WHERE user_id = ?
        """
        values: list[Any] = [user_id]
        if conversation_key is not None:
            query += " AND conversation_key = ?"
            values.append(
                self._normalize_identifier(conversation_key, "conversation_key")
            )
        query += " ORDER BY id DESC LIMIT ?"
        values.append(bounded_limit)
        with closing(self._connect()) as connection:
            rows = connection.execute(query, tuple(values)).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            try:
                reasons = json.loads(row["reasons_json"] or "[]")
            except json.JSONDecodeError:
                reasons = []
            result.append(
                {
                    "id": int(row["id"]),
                    "user_id": str(row["user_id"]),
                    "conversation_key": str(row["conversation_key"]),
                    "state_version": int(row["state_version"]),
                    "turn_index": int(row["turn_index"]),
                    "decision": str(row["decision"]),
                    "candidate_layer": row["candidate_layer"],
                    "candidate_kind": row["candidate_kind"],
                    "candidate_id": row["candidate_id"],
                    "score": row["score"],
                    "threshold": float(row["threshold"]),
                    "explicit_request": bool(row["explicit_request"]),
                    "reasons": reasons if isinstance(reasons, list) else [],
                    "created_at": str(row["created_at"]),
                }
            )
        return result

    def status(self, *, user_id: str) -> list[dict[str, Any]]:
        user_id = self._normalize_identifier(user_id, "user_id")
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT user_id, conversation_key, turn_index, last_allowed_turn,
                       last_memory_kind, last_memory_id, last_decision, updated_at
                FROM memory_recall_conversations
                WHERE user_id = ?
                ORDER BY updated_at DESC
                """,
                (user_id,),
            ).fetchall()
        return [dict(row) for row in rows]
