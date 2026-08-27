from __future__ import annotations

import re
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from ..memory import MemoryStore


_SCHEMA_VERSION = 1
_SCHEMA = """
CREATE TABLE IF NOT EXISTS memory_service_schema (
    name TEXT PRIMARY KEY,
    version INTEGER NOT NULL,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS memory_search_documents (
    source_kind TEXT NOT NULL,
    source_id INTEGER NOT NULL,
    canonical_user_id TEXT NOT NULL,
    persona_id TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    generation INTEGER NOT NULL,
    memory_key TEXT NOT NULL,
    prompt_text TEXT NOT NULL,
    search_text TEXT NOT NULL,
    quality REAL NOT NULL,
    PRIMARY KEY(source_kind, source_id)
);

CREATE INDEX IF NOT EXISTS idx_memory_search_documents_user
ON memory_search_documents(canonical_user_id, active, source_kind);
"""


@dataclass(frozen=True)
class SearchDocument:
    source_kind: str
    source_id: int
    memory_key: str
    prompt_text: str
    search_text: str
    quality: float
    rrf_rank: int = 0


def _aliases(key: str, value: str) -> str:
    normalized = f"{key} {value}".casefold()
    extra: list[str] = []
    if key == "gpu" or "显卡" in normalized or "gpu" in normalized:
        extra.extend(("GPU", "显卡", "AI 显卡", "推理设备", "运行本地模型"))
    if "seed-vc" in normalized:
        extra.extend(("Seed-VC", "变声", "voice conversion"))
    if "gpt-sovits" in normalized:
        extra.extend(("GPT-SoVITS", "语音合成", "TTS"))
    return "、".join(extra)


def _fact_text(key: str, value: str) -> tuple[str, str]:
    prompt = f"{key}={value}"
    aliases = _aliases(key, value)
    search = f"用户的 {key} 是 {value}。关联：{key}、{value}"
    if aliases:
        search += f"、{aliases}"
    return prompt, search


def _preference_text(category: str, value: str, polarity: int) -> tuple[str, str]:
    verb = "喜欢" if polarity > 0 else "不喜欢"
    prompt = f"偏好：用户{verb}{value}"
    return prompt, f"用户在 {category} 上{verb}{value}。关联：{category}、{value}。"


def _episode_text(summary: str) -> tuple[str, str]:
    compact = re.sub(r"\s+", " ", summary).strip()
    return compact[:180], compact[:900]


class FtsMemoryIndex:
    """A fully rebuildable FTS5 projection of active canonical memories."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path).expanduser().resolve()
        self.fts_available = False
        self.tokenizer = "unavailable"
        self.initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA busy_timeout = 3000")
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def initialize(self) -> None:
        from datetime import datetime, timezone

        with closing(self._connect()) as connection:
            connection.executescript(_SCHEMA)
            connection.execute(
                "INSERT OR IGNORE INTO memory_service_schema(name, version, applied_at) VALUES (?, ?, ?)",
                ("memory_service", _SCHEMA_VERSION, datetime.now(timezone.utc).isoformat()),
            )
            try:
                connection.execute(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS memory_search_fts "
                    "USING fts5(search_text, content='memory_search_documents', content_rowid='rowid', tokenize='trigram')"
                )
                self.tokenizer = "trigram"
                self.fts_available = True
            except sqlite3.OperationalError:
                try:
                    connection.execute(
                        "CREATE VIRTUAL TABLE IF NOT EXISTS memory_search_fts "
                        "USING fts5(search_text, content='memory_search_documents', content_rowid='rowid', tokenize='unicode61')"
                    )
                    self.tokenizer = "unicode61"
                    self.fts_available = True
                except sqlite3.OperationalError:
                    self.fts_available = False
            connection.commit()

    def rebuild_user(self, *, canonical_user_id: str, persona_id: str) -> int:
        """Replace only derived rows for one identity; canonical tables are untouched."""
        rows: list[tuple[str, int, str, str, str, float]] = []
        with closing(self._connect()) as connection:
            facts = connection.execute(
                "SELECT id, fact_key, fact_value, confidence FROM facts WHERE user_id = ? AND active = 1",
                (canonical_user_id,),
            ).fetchall()
            preferences = connection.execute(
                "SELECT id, category, value, polarity, confidence FROM preferences WHERE user_id = ? AND active = 1",
                (canonical_user_id,),
            ).fetchall()
            episodes = connection.execute(
                "SELECT id, summary, importance FROM episodes WHERE user_id = ? AND active = 1",
                (canonical_user_id,),
            ).fetchall()
            for row in facts:
                prompt, search = _fact_text(str(row["fact_key"]), str(row["fact_value"]))
                rows.append(("fact", int(row["id"]), str(row["fact_key"]), prompt, search, float(row["confidence"])))
            for row in preferences:
                prompt, search = _preference_text(str(row["category"]), str(row["value"]), int(row["polarity"]))
                rows.append(("preference", int(row["id"]), str(row["category"]), prompt, search, float(row["confidence"])))
            for row in episodes:
                prompt, search = _episode_text(str(row["summary"]))
                rows.append(("episode", int(row["id"]), "milestone", prompt, search, float(row["importance"])))

            connection.execute("BEGIN IMMEDIATE")
            old_rows = connection.execute(
                "SELECT rowid FROM memory_search_documents WHERE canonical_user_id = ?",
                (canonical_user_id,),
            ).fetchall()
            if self.fts_available:
                for old in old_rows:
                    connection.execute("INSERT INTO memory_search_fts(memory_search_fts, rowid, search_text) VALUES('delete', ?, '')", (int(old["rowid"]),))
            connection.execute("DELETE FROM memory_search_documents WHERE canonical_user_id = ?", (canonical_user_id,))
            for generation, row in enumerate(rows, start=1):
                cursor = connection.execute(
                    """
                    INSERT INTO memory_search_documents(
                        source_kind, source_id, canonical_user_id, persona_id, active,
                        generation, memory_key, prompt_text, search_text, quality
                    ) VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, ?)
                    """,
                    (row[0], row[1], canonical_user_id, persona_id, generation, row[2], row[3], row[4], row[5]),
                )
                if self.fts_available:
                    connection.execute(
                        "INSERT INTO memory_search_fts(rowid, search_text) VALUES (?, ?)",
                        (int(cursor.lastrowid), row[4]),
                    )
            connection.commit()
        return len(rows)

    @staticmethod
    def _fts_query(message: str) -> str | None:
        terms = MemoryStore._tokens(message)
        safe = [term.replace('"', '') for term in terms if len(term) >= 2]
        return " OR ".join(f'"{term}"' for term in safe[:12]) or None

    def search(self, *, canonical_user_id: str, message: str, limit: int = 8) -> list[SearchDocument]:
        limit = max(1, min(30, int(limit)))
        documents: dict[tuple[str, int], SearchDocument] = {}
        with closing(self._connect()) as connection:
            exact = connection.execute(
                """
                SELECT source_kind, source_id, memory_key, prompt_text, search_text, quality
                FROM memory_search_documents
                WHERE canonical_user_id = ? AND active = 1
                """,
                (canonical_user_id,),
            ).fetchall()
            tokens = MemoryStore._tokens(message)
            for row in exact:
                text = f"{row['memory_key']} {row['prompt_text']} {row['search_text']}"
                if tokens & MemoryStore._tokens(text):
                    key = (str(row["source_kind"]), int(row["source_id"]))
                    documents[key] = SearchDocument(
                        key[0], key[1], str(row["memory_key"]), str(row["prompt_text"]),
                        str(row["search_text"]), float(row["quality"]), 1,
                    )
            query = self._fts_query(message)
            if self.fts_available and query:
                try:
                    fts = connection.execute(
                        """
                        SELECT d.source_kind, d.source_id, d.memory_key, d.prompt_text,
                               d.search_text, d.quality
                        FROM memory_search_fts f
                        JOIN memory_search_documents d ON d.rowid = f.rowid
                        WHERE f.search_text MATCH ?
                          AND d.canonical_user_id = ? AND d.active = 1
                        ORDER BY bm25(memory_search_fts) ASC LIMIT ?
                        """,
                        (query, canonical_user_id, limit),
                    ).fetchall()
                    for rank, row in enumerate(fts, start=1):
                        key = (str(row["source_kind"]), int(row["source_id"]))
                        previous = documents.get(key)
                        documents[key] = SearchDocument(
                            key[0], key[1], str(row["memory_key"]), str(row["prompt_text"]),
                            str(row["search_text"]), float(row["quality"]),
                            min(previous.rrf_rank if previous else 999, rank),
                        )
                except sqlite3.OperationalError:
                    # FTS is an optional derived acceleration. Exact retrieval remains valid.
                    pass
        return sorted(documents.values(), key=lambda item: (item.rrf_rank, -item.quality, item.source_id))[:limit]

    def status(self) -> dict[str, object]:
        return {"schema_version": _SCHEMA_VERSION, "fts_available": self.fts_available, "fts_tokenizer": self.tokenizer}
