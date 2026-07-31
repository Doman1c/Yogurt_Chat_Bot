from __future__ import annotations

import re
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from .models import MemorySnippet, Relationship


_SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
    user_id TEXT PRIMARY KEY,
    display_name TEXT,
    relationship TEXT NOT NULL DEFAULT 'owner',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS facts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    fact_key TEXT NOT NULL,
    fact_value TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 1.0,
    source TEXT NOT NULL DEFAULT 'manual',
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(user_id, fact_key)
);

CREATE TABLE IF NOT EXISTS preferences (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    category TEXT NOT NULL,
    value TEXT NOT NULL,
    polarity INTEGER NOT NULL DEFAULT 1,
    confidence REAL NOT NULL DEFAULT 1.0,
    source TEXT NOT NULL DEFAULT 'manual',
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(user_id, category, value)
);

CREATE TABLE IF NOT EXISTS episodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    summary TEXT NOT NULL,
    importance REAL NOT NULL DEFAULT 0.5,
    occurred_at TEXT,
    source TEXT NOT NULL DEFAULT 'manual',
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_facts_user ON facts(user_id, active);
CREATE INDEX IF NOT EXISTS idx_preferences_user ON preferences(user_id, active);
CREATE INDEX IF NOT EXISTS idx_episodes_user ON episodes(user_id, active);
"""


class MemoryStore:
    """Small SQLite store for explicit V1 memories.

    V1 does not automatically write memories from chat. That avoids accidental
    pollution. Automatic extraction belongs to V3 and will write candidates
    before promoting anything to stable memory.
    """

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path).expanduser().resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def initialize(self) -> None:
        with closing(self._connect()) as connection:
            connection.executescript(_SCHEMA)
            connection.commit()

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def upsert_user(
        self,
        user_id: str,
        display_name: str | None = None,
        relationship: str | Relationship = Relationship.OWNER,
    ) -> None:
        relationship_value = relationship.value if isinstance(relationship, Relationship) else relationship
        now = self._now()
        with closing(self._connect()) as connection:
            connection.execute(
                """
                INSERT INTO users(user_id, display_name, relationship, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    display_name = COALESCE(excluded.display_name, users.display_name),
                    relationship = excluded.relationship,
                    updated_at = excluded.updated_at
                """,
                (user_id, display_name, relationship_value, now, now),
            )
            connection.commit()

    def get_relationship(self, user_id: str) -> Relationship:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT relationship FROM users WHERE user_id = ?", (user_id,)
            ).fetchone()
        if row is None:
            return Relationship.OWNER
        try:
            return Relationship(row["relationship"])
        except ValueError:
            return Relationship.OWNER

    def add_fact(
        self,
        user_id: str,
        key: str,
        value: str,
        confidence: float = 1.0,
        source: str = "manual",
    ) -> None:
        self.upsert_user(user_id)
        now = self._now()
        with closing(self._connect()) as connection:
            connection.execute(
                """
                INSERT INTO facts(user_id, fact_key, fact_value, confidence, source, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, fact_key) DO UPDATE SET
                    fact_value = excluded.fact_value,
                    confidence = excluded.confidence,
                    source = excluded.source,
                    active = 1,
                    updated_at = excluded.updated_at
                """,
                (user_id, key.strip(), value.strip(), confidence, source, now, now),
            )
            connection.commit()

    def add_preference(
        self,
        user_id: str,
        category: str,
        value: str,
        polarity: int = 1,
        confidence: float = 1.0,
        source: str = "manual",
    ) -> None:
        if polarity not in (-1, 1):
            raise ValueError("polarity must be 1 (likes) or -1 (dislikes)")
        self.upsert_user(user_id)
        now = self._now()
        with closing(self._connect()) as connection:
            connection.execute(
                """
                INSERT INTO preferences(
                    user_id, category, value, polarity, confidence, source, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, category, value) DO UPDATE SET
                    polarity = excluded.polarity,
                    confidence = excluded.confidence,
                    source = excluded.source,
                    active = 1,
                    updated_at = excluded.updated_at
                """,
                (user_id, category.strip(), value.strip(), polarity, confidence, source, now, now),
            )
            connection.commit()

    def add_episode(
        self,
        user_id: str,
        summary: str,
        importance: float = 0.5,
        occurred_at: str | None = None,
        source: str = "manual",
    ) -> int:
        self.upsert_user(user_id)
        with closing(self._connect()) as connection:
            cursor = connection.execute(
                """
                INSERT INTO episodes(user_id, summary, importance, occurred_at, source, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (user_id, summary.strip(), importance, occurred_at, source, self._now()),
            )
            connection.commit()
            return int(cursor.lastrowid)

    def relevant(self, user_id: str, message: str, limit: int = 3) -> list[MemorySnippet]:
        tokens = self._tokens(message)
        candidates: list[MemorySnippet] = []
        with closing(self._connect()) as connection:
            facts = connection.execute(
                """
                SELECT id, fact_key, fact_value, confidence
                FROM facts WHERE user_id = ? AND active = 1
                """,
                (user_id,),
            ).fetchall()
            preferences = connection.execute(
                """
                SELECT id, category, value, polarity, confidence
                FROM preferences WHERE user_id = ? AND active = 1
                """,
                (user_id,),
            ).fetchall()
            episodes = connection.execute(
                """
                SELECT id, summary, importance
                FROM episodes WHERE user_id = ? AND active = 1
                ORDER BY id DESC LIMIT 100
                """,
                (user_id,),
            ).fetchall()

        for row in facts:
            text = f"{row['fact_key']}：{row['fact_value']}"
            score = self._relevance(tokens, text) + float(row["confidence"]) * 0.15
            candidates.append(MemorySnippet("fact", text, score, int(row["id"])))

        for row in preferences:
            verb = "喜欢" if int(row["polarity"]) > 0 else "不喜欢"
            text = f"{row['category']}：用户{verb}{row['value']}"
            score = self._relevance(tokens, text) + float(row["confidence"]) * 0.18
            candidates.append(MemorySnippet("preference", text, score, int(row["id"])))

        for row in episodes:
            text = str(row["summary"])
            score = self._relevance(tokens, text) + float(row["importance"]) * 0.2
            candidates.append(MemorySnippet("episode", text, score, int(row["id"])))

        filtered = [item for item in candidates if item.score >= 0.16]
        filtered.sort(key=lambda item: item.score, reverse=True)
        return filtered[: max(0, limit)]

    def snapshot(self, user_id: str) -> dict[str, object]:
        with closing(self._connect()) as connection:
            user = connection.execute(
                "SELECT * FROM users WHERE user_id = ?", (user_id,)
            ).fetchone()
            facts = connection.execute(
                "SELECT fact_key, fact_value, confidence FROM facts WHERE user_id = ? AND active = 1",
                (user_id,),
            ).fetchall()
            preferences = connection.execute(
                """
                SELECT category, value, polarity, confidence
                FROM preferences WHERE user_id = ? AND active = 1
                """,
                (user_id,),
            ).fetchall()
            episodes = connection.execute(
                """
                SELECT summary, importance, occurred_at
                FROM episodes WHERE user_id = ? AND active = 1 ORDER BY id DESC
                """,
                (user_id,),
            ).fetchall()
        return {
            "user": dict(user) if user else None,
            "facts": [dict(row) for row in facts],
            "preferences": [dict(row) for row in preferences],
            "episodes": [dict(row) for row in episodes],
        }

    @staticmethod
    def _tokens(text: str) -> set[str]:
        normalized = text.lower()
        latin = re.findall(r"[a-z0-9_\-.]{2,}", normalized)
        chinese_chunks = re.findall(r"[\u4e00-\u9fff]{2,}", normalized)
        chinese_bigrams: list[str] = []
        for chunk in chinese_chunks:
            chinese_bigrams.extend(chunk[index : index + 2] for index in range(len(chunk) - 1))
        return set(latin + chinese_chunks + chinese_bigrams)

    @classmethod
    def _relevance(cls, query_tokens: set[str], text: str) -> float:
        if not query_tokens:
            return 0.0
        memory_tokens = cls._tokens(text)
        if not memory_tokens:
            return 0.0
        overlap = len(query_tokens & memory_tokens)
        return overlap / max(1, min(len(query_tokens), len(memory_tokens)))
