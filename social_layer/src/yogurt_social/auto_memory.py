from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import sys
from contextlib import closing
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

from .memory import MemoryStore


_SENSITIVE_MARKERS = (
    "密码",
    "口令",
    "验证码",
    "token",
    "api key",
    "apikey",
    "密钥",
    "私钥",
    "身份证",
    "护照号",
    "银行卡",
    "信用卡",
)
_UPDATE_MARKERS = ("改成", "换成", "现在是", "以后", "不再", "从现在开始", "最新")
_STABLE_MARKERS = ("一直", "长期", "常住", "最喜欢", "以后", "固定", "默认")
_TEMPORAL_MARKERS = ("今天", "刚刚", "这会儿", "暂时", "目前这次")

_KEY_ALIASES = {
    "名字": "name",
    "姓名": "name",
    "昵称": "preferred_name",
    "称呼": "preferred_name",
    "常驻地址": "location",
    "住址": "location",
    "居住地": "location",
    "专业": "major",
    "学校": "school",
    "职业": "job",
    "工作": "job",
    "模型": "model",
    "显卡": "gpu",
    "电脑": "computer",
    "手机": "phone",
    "系统": "operating_system",
    "语言": "language",
    "时区": "timezone",
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS memory_candidates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    candidate_hash TEXT NOT NULL,
    kind TEXT NOT NULL,
    memory_key TEXT NOT NULL,
    memory_value TEXT NOT NULL,
    polarity INTEGER,
    importance REAL NOT NULL,
    confidence REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    conflict_kind TEXT,
    existing_memory_id INTEGER,
    occurrences INTEGER NOT NULL DEFAULT 1,
    source_hash TEXT NOT NULL,
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    promoted_at TEXT,
    UNIQUE(user_id, candidate_hash)
);

CREATE TABLE IF NOT EXISTS memory_revisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    memory_key TEXT NOT NULL,
    previous_value TEXT,
    new_value TEXT,
    action TEXT NOT NULL,
    candidate_id INTEGER REFERENCES memory_candidates(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_memory_candidates_user_status
ON memory_candidates(user_id, status, updated_at);

CREATE INDEX IF NOT EXISTS idx_memory_revisions_user
ON memory_revisions(user_id, created_at);
"""


@dataclass(frozen=True)
class MemoryCandidate:
    kind: str
    key: str
    value: str
    importance: float
    confidence: float
    reason: str
    polarity: int | None = None
    update_signal: bool = False

    def normalized(self) -> "MemoryCandidate":
        return MemoryCandidate(
            kind=self.kind.strip().lower(),
            key=self.key.strip().lower(),
            value=_clean_value(self.value),
            importance=round(min(1.0, max(0.0, self.importance)), 4),
            confidence=round(min(1.0, max(0.0, self.confidence)), 4),
            reason=self.reason.strip()[:300],
            polarity=self.polarity,
            update_signal=self.update_signal,
        )


@dataclass(frozen=True)
class MemoryWriteResult:
    candidate_id: int
    kind: str
    key: str
    value: str
    status: str
    action: str
    importance: float
    occurrences: int
    conflict_kind: str | None = None
    existing_memory_id: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean_value(value: str) -> str:
    cleaned = re.sub(r"\s+", " ", value).strip(" ，。！？!?；;：:")
    cleaned = re.sub(r"(?:啦|了|呢|呀|啊|喵)[～~]?$", "", cleaned).strip()
    return cleaned[:160]


def _source_hash(user_message: str, assistant_reply: str) -> str:
    payload = f"{user_message}\0{assistant_reply}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _candidate_hash(candidate: MemoryCandidate) -> str:
    payload = (
        f"{candidate.kind}\0{candidate.key}\0{candidate.value.casefold()}\0"
        f"{candidate.polarity if candidate.polarity is not None else ''}"
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _contains_sensitive(text: str) -> bool:
    normalized = text.casefold()
    return any(marker in normalized for marker in _SENSITIVE_MARKERS)


def _clauses(text: str) -> list[str]:
    return [
        item.strip()
        for item in re.split(r"[\n。！？!?；;]+", text)
        if item.strip()
    ]


def _importance(
    clause: str,
    *,
    base: float,
    stable: bool = False,
    update: bool = False,
    explicit: bool = True,
) -> float:
    score = base
    if explicit:
        score += 0.05
    if stable or any(marker in clause for marker in _STABLE_MARKERS):
        score += 0.08
    if update or any(marker in clause for marker in _UPDATE_MARKERS):
        score += 0.16
    if any(marker in clause for marker in _TEMPORAL_MARKERS) and not update:
        score -= 0.08
    if len(clause) > 120:
        score -= 0.05
    return round(min(1.0, max(0.0, score)), 4)


class DeterministicMemoryExtractor:
    """Extract bounded first-person memory candidates without another model call."""

    def extract(self, user_message: str) -> list[MemoryCandidate]:
        if not isinstance(user_message, str) or not user_message.strip():
            return []
        if _contains_sensitive(user_message):
            return []

        candidates: list[MemoryCandidate] = []
        for clause in _clauses(user_message):
            candidates.extend(self._facts(clause))
            candidates.extend(self._preferences(clause))
            candidates.extend(self._episodes(clause))

        result: list[MemoryCandidate] = []
        seen: set[tuple[str, str, str, int | None]] = set()
        for raw in candidates:
            candidate = raw.normalized()
            if not candidate.value or len(candidate.value) < 1:
                continue
            if _contains_sensitive(candidate.value):
                continue
            signature = (
                candidate.kind,
                candidate.key,
                candidate.value.casefold(),
                candidate.polarity,
            )
            if signature in seen:
                continue
            seen.add(signature)
            result.append(candidate)
        return result[:8]

    def _facts(self, clause: str) -> Iterable[MemoryCandidate]:
        update = any(marker in clause for marker in _UPDATE_MARKERS)

        patterns = (
            (r"(?:我叫|我的名字是)([^，。！？]{1,30})", "name", 0.82, "explicit_name"),
            (r"(?:以后叫我|以后称呼我|请叫我)([^，。！？]{1,30})", "preferred_name", 0.86, "preferred_name"),
            (r"(?:我现在住在|我住在|我常住)([^，。！？]{1,60})", "location", 0.79, "explicit_location"),
        )
        for pattern, key, base, reason in patterns:
            match = re.search(pattern, clause)
            if match:
                yield MemoryCandidate(
                    "fact",
                    key,
                    match.group(1),
                    _importance(clause, base=base, stable=key in {"name", "preferred_name"}, update=update),
                    0.94,
                    reason,
                    update_signal=update or key == "preferred_name",
                )

        generic = re.search(
            r"我的(名字|姓名|昵称|称呼|常驻地址|住址|居住地|专业|学校|职业|工作|模型|显卡|电脑|手机|系统|语言|时区)"
            r"(?:现在)?(?:是|改成了|换成了|为)([^，。！？]{1,80})",
            clause,
        )
        if generic:
            label = generic.group(1)
            key = _KEY_ALIASES[label]
            yield MemoryCandidate(
                "fact",
                key,
                generic.group(2),
                _importance(clause, base=0.74, stable=key in {"name", "preferred_name", "location"}, update=update),
                0.9,
                f"explicit_{key}",
                update_signal=update,
            )

        school = re.search(r"我在([^，。！？]{2,50})(?:上学|读书|读研|读本科|读博士)", clause)
        if school:
            yield MemoryCandidate(
                "fact",
                "school",
                school.group(1),
                _importance(clause, base=0.76, stable=True, update=update),
                0.88,
                "school_affiliation",
                update_signal=update,
            )

    def _preferences(self, clause: str) -> Iterable[MemoryCandidate]:
        update = any(marker in clause for marker in _UPDATE_MARKERS)
        assistant = re.search(r"我希望你([^，。！？]{2,100})", clause)
        if assistant:
            yield MemoryCandidate(
                "preference",
                "assistant_behavior",
                assistant.group(1),
                _importance(clause, base=0.82, stable=True, update=update),
                0.92,
                "assistant_behavior_preference",
                polarity=1,
                update_signal=True,
            )

        positive = re.search(r"我(?:最)?(?:喜欢|偏好|爱用|更喜欢)([^，。！？]{1,80})", clause)
        if positive:
            yield MemoryCandidate(
                "preference",
                "general",
                positive.group(1),
                _importance(clause, base=0.70, stable="最喜欢" in clause, update=update),
                0.88,
                "explicit_like",
                polarity=1,
                update_signal=update,
            )

        negative = re.search(r"我(?:不喜欢|讨厌|不爱|不想要)([^，。！？]{1,80})", clause)
        if negative:
            yield MemoryCandidate(
                "preference",
                "general",
                negative.group(1),
                _importance(clause, base=0.72, stable=True, update=update),
                0.9,
                "explicit_dislike",
                polarity=-1,
                update_signal=update,
            )

    def _episodes(self, clause: str) -> Iterable[MemoryCandidate]:
        if len(clause) < 5 or len(clause) > 140:
            return
        if not re.search(r"我.{0,10}(?:终于|已经|刚刚)?(?:完成|通过|拿到|成功|加入|开始|毕业|部署|修好|弄好)", clause):
            return
        update = any(marker in clause for marker in _UPDATE_MARKERS)
        yield MemoryCandidate(
            "episode",
            "milestone",
            clause,
            _importance(clause, base=0.64, stable=False, update=update),
            0.84,
            "personal_milestone",
            update_signal=update,
        )


class AutoMemoryStore:
    def __init__(self, db_path: str | Path, promotion_threshold: float = 0.78) -> None:
        self.db_path = Path(db_path).expanduser().resolve()
        self.base = MemoryStore(self.db_path)
        self.promotion_threshold = min(1.0, max(0.0, promotion_threshold))
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

    def process_chat(
        self,
        *,
        user_id: str,
        user_message: str,
        assistant_reply: str = "",
        extractor: DeterministicMemoryExtractor | None = None,
    ) -> list[MemoryWriteResult]:
        self.base.upsert_user(user_id)
        source_hash = _source_hash(user_message, assistant_reply)
        candidates = (extractor or DeterministicMemoryExtractor()).extract(user_message)
        return [self._process_candidate(user_id, candidate, source_hash) for candidate in candidates]

    def _process_candidate(
        self,
        user_id: str,
        candidate: MemoryCandidate,
        source_hash: str,
    ) -> MemoryWriteResult:
        candidate = candidate.normalized()
        now = _now()
        digest = _candidate_hash(candidate)
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT * FROM memory_candidates WHERE user_id = ? AND candidate_hash = ?",
                (user_id, digest),
            ).fetchone()
            if row is None:
                cursor = connection.execute(
                    """
                    INSERT INTO memory_candidates(
                        user_id, candidate_hash, kind, memory_key, memory_value, polarity,
                        importance, confidence, status, occurrences, source_hash, reason,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', 1, ?, ?, ?, ?)
                    """,
                    (
                        user_id,
                        digest,
                        candidate.kind,
                        candidate.key,
                        candidate.value,
                        candidate.polarity,
                        candidate.importance,
                        candidate.confidence,
                        source_hash,
                        candidate.reason,
                        now,
                        now,
                    ),
                )
                candidate_id = int(cursor.lastrowid)
                occurrences = 1
                effective_importance = candidate.importance
            else:
                candidate_id = int(row["id"])
                occurrences = int(row["occurrences"]) + 1
                effective_importance = min(
                    1.0,
                    max(float(row["importance"]), candidate.importance)
                    + min(0.18, (occurrences - 1) * 0.06),
                )
                connection.execute(
                    """
                    UPDATE memory_candidates SET
                        importance = ?, confidence = MAX(confidence, ?), occurrences = ?,
                        source_hash = ?, reason = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        effective_importance,
                        candidate.confidence,
                        occurrences,
                        source_hash,
                        candidate.reason,
                        now,
                        candidate_id,
                    ),
                )
            connection.commit()

        if candidate.kind == "fact":
            return self._resolve_fact(user_id, candidate_id, candidate, effective_importance, occurrences)
        if candidate.kind == "preference":
            return self._resolve_preference(
                user_id, candidate_id, candidate, effective_importance, occurrences
            )
        return self._resolve_episode(user_id, candidate_id, candidate, effective_importance, occurrences)

    def _resolve_fact(
        self,
        user_id: str,
        candidate_id: int,
        candidate: MemoryCandidate,
        importance: float,
        occurrences: int,
    ) -> MemoryWriteResult:
        with closing(self._connect()) as connection:
            existing = connection.execute(
                """
                SELECT id, fact_value, confidence FROM facts
                WHERE user_id = ? AND fact_key = ? AND active = 1
                """,
                (user_id, candidate.key),
            ).fetchone()

            if existing is None:
                if importance >= self.promotion_threshold:
                    now = _now()
                    cursor = connection.execute(
                        """
                        INSERT INTO facts(
                            user_id, fact_key, fact_value, confidence, source,
                            active, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, 'auto-v3', 1, ?, ?)
                        """,
                        (user_id, candidate.key, candidate.value, candidate.confidence, now, now),
                    )
                    memory_id = int(cursor.lastrowid)
                    self._finish_candidate(connection, candidate_id, "promoted", None, memory_id)
                    self._revision(
                        connection, user_id, "fact", candidate.key, None, candidate.value, "inserted", candidate_id
                    )
                    connection.commit()
                    return self._result(candidate_id, candidate, "promoted", "inserted", importance, occurrences, existing_id=memory_id)
                self._finish_candidate(connection, candidate_id, "pending", None, None)
                connection.commit()
                return self._result(candidate_id, candidate, "pending", "candidate_only", importance, occurrences)

            existing_id = int(existing["id"])
            previous = str(existing["fact_value"])
            if previous.casefold() == candidate.value.casefold():
                connection.execute(
                    """
                    UPDATE facts SET confidence = MAX(confidence, ?), updated_at = ? WHERE id = ?
                    """,
                    (candidate.confidence, _now(), existing_id),
                )
                self._finish_candidate(connection, candidate_id, "merged", "same_value", existing_id)
                self._revision(
                    connection, user_id, "fact", candidate.key, previous, candidate.value, "reaffirmed", candidate_id
                )
                connection.commit()
                return self._result(candidate_id, candidate, "merged", "reaffirmed", importance, occurrences, "same_value", existing_id)

            can_replace = candidate.update_signal or importance >= 0.9
            if can_replace:
                connection.execute(
                    """
                    UPDATE facts SET fact_value = ?, confidence = ?, source = 'auto-v3',
                        active = 1, updated_at = ? WHERE id = ?
                    """,
                    (candidate.value, candidate.confidence, _now(), existing_id),
                )
                self._finish_candidate(connection, candidate_id, "promoted", "replacement", existing_id)
                self._revision(
                    connection, user_id, "fact", candidate.key, previous, candidate.value, "updated", candidate_id
                )
                connection.commit()
                return self._result(candidate_id, candidate, "promoted", "updated", importance, occurrences, "replacement", existing_id)

            self._finish_candidate(connection, candidate_id, "conflict", "value_conflict", existing_id)
            connection.commit()
            return self._result(candidate_id, candidate, "conflict", "needs_confirmation", importance, occurrences, "value_conflict", existing_id)

    def _resolve_preference(
        self,
        user_id: str,
        candidate_id: int,
        candidate: MemoryCandidate,
        importance: float,
        occurrences: int,
    ) -> MemoryWriteResult:
        polarity = candidate.polarity if candidate.polarity in (-1, 1) else 1
        with closing(self._connect()) as connection:
            existing = connection.execute(
                """
                SELECT id, polarity FROM preferences
                WHERE user_id = ? AND category = ? AND value = ? AND active = 1
                """,
                (user_id, candidate.key, candidate.value),
            ).fetchone()
            if existing is None:
                if importance >= self.promotion_threshold:
                    now = _now()
                    cursor = connection.execute(
                        """
                        INSERT INTO preferences(
                            user_id, category, value, polarity, confidence, source,
                            active, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, 'auto-v3', 1, ?, ?)
                        """,
                        (user_id, candidate.key, candidate.value, polarity, candidate.confidence, now, now),
                    )
                    memory_id = int(cursor.lastrowid)
                    self._finish_candidate(connection, candidate_id, "promoted", None, memory_id)
                    self._revision(
                        connection,
                        user_id,
                        "preference",
                        candidate.key,
                        None,
                        f"{polarity}:{candidate.value}",
                        "inserted",
                        candidate_id,
                    )
                    connection.commit()
                    return self._result(candidate_id, candidate, "promoted", "inserted", importance, occurrences, existing_id=memory_id)
                self._finish_candidate(connection, candidate_id, "pending", None, None)
                connection.commit()
                return self._result(candidate_id, candidate, "pending", "candidate_only", importance, occurrences)

            existing_id = int(existing["id"])
            previous_polarity = int(existing["polarity"])
            if previous_polarity == polarity:
                connection.execute(
                    "UPDATE preferences SET confidence = MAX(confidence, ?), updated_at = ? WHERE id = ?",
                    (candidate.confidence, _now(), existing_id),
                )
                self._finish_candidate(connection, candidate_id, "merged", "same_preference", existing_id)
                connection.commit()
                return self._result(candidate_id, candidate, "merged", "reaffirmed", importance, occurrences, "same_preference", existing_id)

            if candidate.update_signal or importance >= 0.88:
                connection.execute(
                    """
                    UPDATE preferences SET polarity = ?, confidence = ?, source = 'auto-v3',
                        updated_at = ? WHERE id = ?
                    """,
                    (polarity, candidate.confidence, _now(), existing_id),
                )
                self._finish_candidate(connection, candidate_id, "promoted", "polarity_reversal", existing_id)
                self._revision(
                    connection,
                    user_id,
                    "preference",
                    candidate.key,
                    f"{previous_polarity}:{candidate.value}",
                    f"{polarity}:{candidate.value}",
                    "updated",
                    candidate_id,
                )
                connection.commit()
                return self._result(candidate_id, candidate, "promoted", "updated", importance, occurrences, "polarity_reversal", existing_id)

            self._finish_candidate(connection, candidate_id, "conflict", "polarity_conflict", existing_id)
            connection.commit()
            return self._result(candidate_id, candidate, "conflict", "needs_confirmation", importance, occurrences, "polarity_conflict", existing_id)

    def _resolve_episode(
        self,
        user_id: str,
        candidate_id: int,
        candidate: MemoryCandidate,
        importance: float,
        occurrences: int,
    ) -> MemoryWriteResult:
        with closing(self._connect()) as connection:
            existing = connection.execute(
                "SELECT id FROM episodes WHERE user_id = ? AND summary = ? AND active = 1",
                (user_id, candidate.value),
            ).fetchone()
            if existing is not None:
                existing_id = int(existing["id"])
                self._finish_candidate(connection, candidate_id, "merged", "same_episode", existing_id)
                connection.commit()
                return self._result(candidate_id, candidate, "merged", "reaffirmed", importance, occurrences, "same_episode", existing_id)

            episode_threshold = max(self.promotion_threshold, 0.82)
            if importance >= episode_threshold:
                cursor = connection.execute(
                    """
                    INSERT INTO episodes(user_id, summary, importance, source, active, created_at)
                    VALUES (?, ?, ?, 'auto-v3', 1, ?)
                    """,
                    (user_id, candidate.value, importance, _now()),
                )
                memory_id = int(cursor.lastrowid)
                self._finish_candidate(connection, candidate_id, "promoted", None, memory_id)
                self._revision(
                    connection, user_id, "episode", candidate.key, None, candidate.value, "inserted", candidate_id
                )
                connection.commit()
                return self._result(candidate_id, candidate, "promoted", "inserted", importance, occurrences, existing_id=memory_id)

            self._finish_candidate(connection, candidate_id, "pending", None, None)
            connection.commit()
            return self._result(candidate_id, candidate, "pending", "candidate_only", importance, occurrences)

    @staticmethod
    def _finish_candidate(
        connection: sqlite3.Connection,
        candidate_id: int,
        status: str,
        conflict_kind: str | None,
        existing_id: int | None,
    ) -> None:
        now = _now()
        connection.execute(
            """
            UPDATE memory_candidates SET status = ?, conflict_kind = ?, existing_memory_id = ?,
                updated_at = ?, promoted_at = CASE WHEN ? = 'promoted' THEN ? ELSE promoted_at END
            WHERE id = ?
            """,
            (status, conflict_kind, existing_id, now, status, now, candidate_id),
        )

    @staticmethod
    def _revision(
        connection: sqlite3.Connection,
        user_id: str,
        kind: str,
        key: str,
        previous: str | None,
        new: str | None,
        action: str,
        candidate_id: int,
    ) -> None:
        connection.execute(
            """
            INSERT INTO memory_revisions(
                user_id, kind, memory_key, previous_value, new_value,
                action, candidate_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (user_id, kind, key, previous, new, action, candidate_id, _now()),
        )

    @staticmethod
    def _result(
        candidate_id: int,
        candidate: MemoryCandidate,
        status: str,
        action: str,
        importance: float,
        occurrences: int,
        conflict: str | None = None,
        existing_id: int | None = None,
    ) -> MemoryWriteResult:
        return MemoryWriteResult(
            candidate_id=candidate_id,
            kind=candidate.kind,
            key=candidate.key,
            value=candidate.value,
            status=status,
            action=action,
            importance=round(importance, 4),
            occurrences=occurrences,
            conflict_kind=conflict,
            existing_memory_id=existing_id,
        )

    def list_candidates(self, user_id: str, status: str | None = None) -> list[dict[str, Any]]:
        query = """
            SELECT id, kind, memory_key, memory_value, polarity, importance, confidence,
                   status, conflict_kind, existing_memory_id, occurrences, reason,
                   created_at, updated_at, promoted_at
            FROM memory_candidates WHERE user_id = ?
        """
        values: list[Any] = [user_id]
        if status:
            query += " AND status = ?"
            values.append(status)
        query += " ORDER BY updated_at DESC, id DESC"
        with closing(self._connect()) as connection:
            return [dict(row) for row in connection.execute(query, values).fetchall()]


def _read_request(stream: Any) -> Mapping[str, Any]:
    raw = stream.read()
    if not raw:
        raise ValueError("expected one JSON request on stdin")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("request must be an object")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Yogurt V3 deterministic automatic memory")
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--promotion-threshold", type=float, default=0.78)
    parser.add_argument("--pretty", action="store_true")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        request = _read_request(sys.stdin)
        user_id = request.get("user_id")
        user_message = request.get("user_message")
        assistant_reply = request.get("assistant_reply", "")
        if not isinstance(user_id, str) or not user_id.strip():
            raise ValueError("user_id must be a non-empty string")
        if not isinstance(user_message, str) or not isinstance(assistant_reply, str):
            raise ValueError("message fields must be strings")
        store = AutoMemoryStore(args.db, promotion_threshold=args.promotion_threshold)
        results = store.process_chat(
            user_id=user_id.strip(),
            user_message=user_message,
            assistant_reply=assistant_reply,
        )
        payload = {
            "ok": True,
            "candidate_count": len(results),
            "promoted_count": sum(item.status == "promoted" for item in results),
            "pending_count": sum(item.status == "pending" for item in results),
            "conflict_count": sum(item.status == "conflict" for item in results),
            "results": [item.to_dict() for item in results],
        }
    except Exception as exc:
        payload = {"ok": False, "error": type(exc).__name__}
    print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None))
    return 0 if payload.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
