from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional


_PROJECT_STATE_SCHEMA = """
CREATE TABLE IF NOT EXISTS project_state (
    user_id TEXT NOT NULL,
    project_key TEXT NOT NULL,
    current_version TEXT,
    plugin_version TEXT,
    current_stage TEXT,
    completed_json TEXT NOT NULL DEFAULT '[]',
    next_json TEXT NOT NULL DEFAULT '[]',
    source TEXT NOT NULL DEFAULT 'repository_snapshot',
    state_version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(user_id, project_key)
);

CREATE TABLE IF NOT EXISTS project_state_revision (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    project_key TEXT NOT NULL,
    state_version INTEGER NOT NULL,
    changed_fields_json TEXT NOT NULL,
    source TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_project_state_revision_lookup
ON project_state_revision(user_id, project_key, id DESC);
"""

_MAX_VERSION_CHARS = 48
_MAX_STAGE_CHARS = 180
_MAX_ITEM_CHARS = 220
_MAX_COMPLETED = 12
_MAX_NEXT = 8


def _bounded_text(value: Any, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    text = " ".join(value.strip().split())
    return text[:limit] if text else None


def _bounded_items(values: Any, limit: int) -> tuple[str, ...]:
    if not isinstance(values, (list, tuple)):
        return ()
    result: list[str] = []
    for value in values:
        text = _bounded_text(value, _MAX_ITEM_CHARS)
        if text and text not in result:
            result.append(text)
        if len(result) >= limit:
            break
    return tuple(result)


def _json_items(value: Any, limit: int) -> tuple[str, ...]:
    try:
        raw = json.loads(value) if isinstance(value, str) else []
    except (TypeError, ValueError):
        raw = []
    return _bounded_items(raw, limit)


@dataclass(frozen=True)
class ProjectState:
    user_id: str
    project_key: str
    current_version: str | None
    plugin_version: str | None
    current_stage: str | None
    completed: tuple[str, ...]
    next_items: tuple[str, ...]
    source: str
    state_version: int
    updated_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "project_key": self.project_key,
            "current_version": self.current_version,
            "plugin_version": self.plugin_version,
            "current_stage": self.current_stage,
            "completed": list(self.completed),
            "next": list(self.next_items),
            "source": self.source,
            "state_version": self.state_version,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True)
class ProjectStatePatch:
    current_version: str | None = None
    plugin_version: str | None = None
    current_stage: str | None = None
    completed_add: tuple[str, ...] = ()
    next_items: tuple[str, ...] = ()
    replace_next: bool = False

    def empty(self) -> bool:
        return not any(
            (
                self.current_version,
                self.plugin_version,
                self.current_stage,
                self.completed_add,
                self.next_items,
                self.replace_next,
            )
        )


class ProjectStateStore:
    """Persistent mutable project state, separate from long-term user memory."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path).expanduser().resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def initialize(self) -> None:
        with closing(self._connect()) as connection:
            connection.executescript(_PROJECT_STATE_SCHEMA)
            connection.commit()

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _normalize_id(value: str, name: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} must be a non-empty string")
        return value.strip()[:160]

    @staticmethod
    def _from_row(row: sqlite3.Row) -> ProjectState:
        return ProjectState(
            user_id=str(row["user_id"]),
            project_key=str(row["project_key"]),
            current_version=_bounded_text(row["current_version"], _MAX_VERSION_CHARS),
            plugin_version=_bounded_text(row["plugin_version"], _MAX_VERSION_CHARS),
            current_stage=_bounded_text(row["current_stage"], _MAX_STAGE_CHARS),
            completed=_json_items(row["completed_json"], _MAX_COMPLETED),
            next_items=_json_items(row["next_json"], _MAX_NEXT),
            source=str(row["source"]),
            state_version=max(1, int(row["state_version"])),
            updated_at=str(row["updated_at"]),
        )

    def get(self, user_id: str, project_key: str) -> ProjectState | None:
        user_id = self._normalize_id(user_id, "user_id")
        project_key = self._normalize_id(project_key, "project_key")
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT * FROM project_state WHERE user_id = ? AND project_key = ?",
                (user_id, project_key),
            ).fetchone()
        return self._from_row(row) if row is not None else None

    @staticmethod
    def _merge_completed(
        existing: tuple[str, ...], additions: tuple[str, ...]
    ) -> tuple[str, ...]:
        result = list(existing)
        for item in _bounded_items(additions, _MAX_COMPLETED):
            if item not in result:
                result.append(item)
        return tuple(result[-_MAX_COMPLETED:])

    def update(
        self,
        user_id: str,
        project_key: str,
        patch: ProjectStatePatch,
        *,
        source: str = "user_explicit",
    ) -> ProjectState:
        user_id = self._normalize_id(user_id, "user_id")
        project_key = self._normalize_id(project_key, "project_key")
        source = _bounded_text(source, 64) or "user_explicit"
        if patch.empty():
            current = self.get(user_id, project_key)
            if current is None:
                raise ValueError("project state patch is empty")
            return current

        now = self._now()
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM project_state WHERE user_id = ? AND project_key = ?",
                (user_id, project_key),
            ).fetchone()
            before = self._from_row(row) if row is not None else None

            current_version = (
                _bounded_text(patch.current_version, _MAX_VERSION_CHARS)
                if patch.current_version is not None
                else (before.current_version if before else None)
            )
            plugin_version = (
                _bounded_text(patch.plugin_version, _MAX_VERSION_CHARS)
                if patch.plugin_version is not None
                else (before.plugin_version if before else None)
            )
            current_stage = (
                _bounded_text(patch.current_stage, _MAX_STAGE_CHARS)
                if patch.current_stage is not None
                else (before.current_stage if before else None)
            )
            completed = self._merge_completed(
                before.completed if before else (), patch.completed_add
            )
            if patch.replace_next:
                next_items = _bounded_items(patch.next_items, _MAX_NEXT)
            elif patch.next_items:
                next_items = _bounded_items(patch.next_items, _MAX_NEXT)
            else:
                next_items = before.next_items if before else ()

            changed: dict[str, Any] = {}
            comparisons = {
                "current_version": (before.current_version if before else None, current_version),
                "plugin_version": (before.plugin_version if before else None, plugin_version),
                "current_stage": (before.current_stage if before else None, current_stage),
                "completed": (list(before.completed) if before else [], list(completed)),
                "next": (list(before.next_items) if before else [], list(next_items)),
            }
            for field, (old, new) in comparisons.items():
                if old != new:
                    changed[field] = {"old": old, "new": new}

            if before is not None and not changed:
                connection.commit()
                return before

            state_version = (before.state_version + 1) if before else 1
            created_at = row["created_at"] if row is not None else now
            connection.execute(
                """
                INSERT INTO project_state(
                    user_id, project_key, current_version, plugin_version,
                    current_stage, completed_json, next_json, source,
                    state_version, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, project_key) DO UPDATE SET
                    current_version = excluded.current_version,
                    plugin_version = excluded.plugin_version,
                    current_stage = excluded.current_stage,
                    completed_json = excluded.completed_json,
                    next_json = excluded.next_json,
                    source = excluded.source,
                    state_version = excluded.state_version,
                    updated_at = excluded.updated_at
                """,
                (
                    user_id,
                    project_key,
                    current_version,
                    plugin_version,
                    current_stage,
                    json.dumps(list(completed), ensure_ascii=False),
                    json.dumps(list(next_items), ensure_ascii=False),
                    source,
                    state_version,
                    created_at,
                    now,
                ),
            )
            connection.execute(
                """
                INSERT INTO project_state_revision(
                    user_id, project_key, state_version,
                    changed_fields_json, source, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    project_key,
                    state_version,
                    json.dumps(changed, ensure_ascii=False),
                    source,
                    now,
                ),
            )
            connection.commit()

        current = self.get(user_id, project_key)
        if current is None:
            raise RuntimeError("project state write did not persist")
        return current

    def revisions(
        self, user_id: str, project_key: str, limit: int = 20
    ) -> list[dict[str, Any]]:
        user_id = self._normalize_id(user_id, "user_id")
        project_key = self._normalize_id(project_key, "project_key")
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT state_version, changed_fields_json, source, created_at
                FROM project_state_revision
                WHERE user_id = ? AND project_key = ?
                ORDER BY id DESC LIMIT ?
                """,
                (user_id, project_key, max(0, min(100, int(limit)))),
            ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            try:
                changed = json.loads(row["changed_fields_json"])
            except (TypeError, ValueError):
                changed = {}
            result.append(
                {
                    "state_version": int(row["state_version"]),
                    "changed_fields": changed if isinstance(changed, dict) else {},
                    "source": str(row["source"]),
                    "created_at": str(row["created_at"]),
                }
            )
        return result


def project_key_from_status(status: Mapping[str, Any] | None) -> str:
    project = _bounded_text(status.get("project"), 80) if status else None
    if project:
        normalized = "-".join(project.casefold().replace("_", " ").split())
        return normalized[:80] or "yogurt-chat-bot"
    return "yogurt-chat-bot"
