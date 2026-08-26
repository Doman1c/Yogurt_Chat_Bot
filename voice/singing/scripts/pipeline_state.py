"""Durable, dependency-free state handling for the Singing inbox."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


class PipelineAlreadyRunning(RuntimeError):
    pass


class PipelineLock:
    """An owner-PID lock that can safely recover after a process crash."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.owned = False

    @staticmethod
    def _pid_is_alive(pid: int) -> bool:
        if pid <= 0:
            return False
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({"pid": os.getpid(), "created_at": utc_now()})
        for _ in range(2):
            try:
                fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    handle.write(payload)
                self.owned = True
                return
            except FileExistsError:
                try:
                    current = json.loads(self.path.read_text(encoding="utf-8"))
                    alive = self._pid_is_alive(int(current.get("pid", 0)))
                except (OSError, ValueError, json.JSONDecodeError):
                    alive = False
                if alive:
                    raise PipelineAlreadyRunning("PIPELINE_ALREADY_RUNNING")
                # A dead owner is a crash artifact, not a concurrent owner.
                try:
                    self.path.unlink()
                except FileNotFoundError:
                    pass
        raise PipelineAlreadyRunning("PIPELINE_ALREADY_RUNNING")

    def release(self) -> None:
        if self.owned:
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass
            self.owned = False

    def __enter__(self) -> "PipelineLock":
        self.acquire()
        return self

    def __exit__(self, *_: object) -> None:
        self.release()


class JobHistory:
    def __init__(self, database: Path) -> None:
        self.database = database
        database.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(database)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS jobs (
              job_id TEXT PRIMARY KEY,
              attempt_id TEXT NOT NULL,
              sha256 TEXT NOT NULL,
              original_filename TEXT NOT NULL,
              processing_path TEXT NOT NULL,
              archive_path TEXT,
              output_dir TEXT,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              state TEXT NOT NULL,
              stage TEXT NOT NULL,
              error TEXT,
              force_run INTEGER NOT NULL DEFAULT 0,
              pipeline_version TEXT NOT NULL,
              canonical_job_id TEXT
            );
            CREATE TABLE IF NOT EXISTS attempts (
              attempt_id TEXT PRIMARY KEY,
              job_id TEXT NOT NULL,
              started_at TEXT NOT NULL,
              finished_at TEXT,
              state TEXT NOT NULL,
              stage TEXT NOT NULL,
              error TEXT,
              FOREIGN KEY(job_id) REFERENCES jobs(job_id)
            );
            CREATE INDEX IF NOT EXISTS idx_jobs_sha_state ON jobs(sha256, state);
            """
        )
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "JobHistory":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def next_attempt(self, sha256: str) -> int:
        row = self.connection.execute("SELECT COUNT(*) AS total FROM jobs WHERE sha256 = ?", (sha256,)).fetchone()
        return int(row["total"]) + 1

    def successful_for_sha(self, sha256: str) -> sqlite3.Row | None:
        return self.connection.execute(
            "SELECT * FROM jobs WHERE sha256 = ? AND state = 'success' ORDER BY updated_at DESC LIMIT 1", (sha256,)
        ).fetchone()

    def create(self, *, job_id: str, attempt_id: str, sha256: str, original_filename: str,
               processing_path: Path, force: bool, pipeline_version: str, state: str = "claimed",
               stage: str = "CLAIMED", canonical_job_id: str | None = None) -> None:
        stamp = utc_now()
        self.connection.execute(
            """INSERT INTO jobs(job_id,attempt_id,sha256,original_filename,processing_path,created_at,updated_at,
                                state,stage,force_run,pipeline_version,canonical_job_id)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (job_id, attempt_id, sha256, original_filename, str(processing_path), stamp, stamp,
             state, stage, int(force), pipeline_version, canonical_job_id),
        )
        self.connection.execute(
            "INSERT INTO attempts(attempt_id,job_id,started_at,state,stage) VALUES(?,?,?,?,?)",
            (attempt_id, job_id, stamp, state, stage),
        )
        self.connection.commit()

    def update(self, job_id: str, *, state: str, stage: str, error: str | None = None,
               archive_path: Path | None = None, output_dir: Path | None = None) -> None:
        stamp = utc_now()
        self.connection.execute(
            """UPDATE jobs SET state=?, stage=?, error=?, archive_path=COALESCE(?, archive_path),
                            output_dir=COALESCE(?, output_dir), updated_at=? WHERE job_id=?""",
            (state, stage, error, str(archive_path) if archive_path else None,
             str(output_dir) if output_dir else None, stamp, job_id),
        )
        self.connection.execute(
            """UPDATE attempts SET state=?,stage=?,error=?,finished_at=CASE WHEN ? IN ('success','failed','recoverable')
               THEN ? ELSE finished_at END WHERE job_id=?""",
            (state, stage, error, state, stamp, job_id),
        )
        self.connection.commit()

    def recover_interrupted(self) -> list[dict[str, str]]:
        rows = self.connection.execute(
            "SELECT job_id,processing_path,state FROM jobs WHERE state IN ('claimed','running')"
        ).fetchall()
        results: list[dict[str, str]] = []
        for row in rows:
            source_exists = Path(row["processing_path"]).is_file()
            error = ("recoverable interrupted job; run with --resume" if source_exists
                     else "recoverable state/files mismatch: processing source is missing")
            self.update(row["job_id"], state="recoverable", stage="RECOVERY_REQUIRED", error=error)
            results.append({"job_id": row["job_id"], "error": error})
        return results

    def resumable(self) -> list[sqlite3.Row]:
        return self.connection.execute(
            "SELECT * FROM jobs WHERE state IN ('failed','recoverable') ORDER BY updated_at"
        ).fetchall()
