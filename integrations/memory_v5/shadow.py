"""Isolation and privacy boundaries for an R3-C Memory V5 shadow runtime."""

from __future__ import annotations

import hashlib
import json
import os
import socket
import sqlite3
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import urlopen

from yogurt_contracts.foundation.identifiers import RuntimeInstanceId


_PRODUCTION_MEMORY_PORT = 17621


def _scope_hash(scope_reference: str) -> str:
    return hashlib.sha256(scope_reference.encode("utf-8")).hexdigest()


def copy_isolated_memory_database(source: Path, destination: Path) -> Path:
    """Copy a closed seed DB into a shadow-owned destination without opening it."""

    source_path = source.resolve()
    destination_path = destination.resolve()
    if source_path == destination_path:
        raise ValueError("shadow database destination must differ from source")
    if not source_path.is_file():
        raise ValueError("shadow database source must exist")
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    if destination_path.exists():
        raise ValueError("shadow database destination must not already exist")
    # ``backup`` sees committed WAL content, unlike a filesystem copy of the
    # main database file, and never writes to the protected source database.
    with sqlite3.connect(str(source_path)) as source_connection:
        with sqlite3.connect(str(destination_path)) as destination_connection:
            source_connection.backup(destination_connection)
    return destination_path


@dataclass(frozen=True)
class MemoryV5ShadowEndpoint:
    """Independent local endpoint identity; production port 17621 is impossible here."""

    base_url: str
    logical_provider_domain: str
    runtime_instance_id: RuntimeInstanceId
    runtime_generation: int
    audit: "ShadowAuditStream"

    def __post_init__(self) -> None:
        parsed = urlparse(self.base_url)
        if parsed.scheme != "http" or parsed.hostname != "127.0.0.1":
            raise ValueError("Memory V5 shadow endpoint must be a local HTTP endpoint")
        if parsed.port is None or parsed.port == _PRODUCTION_MEMORY_PORT:
            raise ValueError("Memory V5 shadow endpoint must not use production port 17621")
        if parsed.path not in {"", "/"} or not self.logical_provider_domain:
            raise ValueError("Memory V5 shadow endpoint is invalid")
        if self.runtime_generation < 1:
            raise ValueError("Memory V5 shadow runtime generation must be positive")


class ShadowAuditStream:
    """Append-only JSONL audit that rejects raw identity and message fields."""

    _FORBIDDEN_FIELDS = {"message", "text", "user_id", "persona_id", "conversation_key", "raw"}

    def __init__(self, path: Path) -> None:
        self.path = path.resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, event_type: str, fields: Dict[str, Any]) -> None:
        if not event_type or any(key in self._FORBIDDEN_FIELDS for key in fields):
            raise ValueError("shadow audit may not contain raw identity or text")
        event = {"event_type": event_type, **fields}
        encoded = json.dumps(event, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        with self.path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(encoded + "\n")

    def record_observation(
        self,
        *,
        operation_id: str,
        scope_reference: str,
        logical_provider_domain: str,
        binding_id: str,
        runtime_generation: int,
        observation_sequence: int,
        dispatch_certainty: str,
        causal_resolution: str,
        history_complete: bool,
        latency_ms: int,
    ) -> None:
        self.append("memory_observation", {
            "operation_id": operation_id,
            "scope_hash": _scope_hash(scope_reference),
            "logical_provider_domain": logical_provider_domain,
            "binding_id": binding_id,
            "runtime_generation": runtime_generation,
            "observation_sequence": observation_sequence,
            "dispatch_certainty": dispatch_certainty,
            "causal_resolution": causal_resolution,
            "history_complete": history_complete,
            "latency_ms": latency_ms,
        })

    def record_recall(
        self,
        *,
        scope_reference: str,
        logical_provider_domain: str,
        binding_id: str,
        runtime_generation: int,
        recall_eligibility: str,
        provider_call_outcome: Optional[str],
        semantic_recall_decision: Optional[str],
        candidate_count: int,
        self_recall_filtered_count: int,
        latency_ms: int,
        stage: str,
    ) -> None:
        self.append("memory_recall", {
            "scope_hash": _scope_hash(scope_reference),
            "logical_provider_domain": logical_provider_domain,
            "binding_id": binding_id,
            "runtime_generation": runtime_generation,
            "recall_eligibility": recall_eligibility,
            "provider_call_outcome": provider_call_outcome,
            "semantic_recall_decision": semantic_recall_decision,
            "candidate_count": candidate_count,
            "self_recall_filtered_count": self_recall_filtered_count,
            "latency_ms": latency_ms,
            "stage": stage,
        })


class IsolatedMemoryV5Process:
    """Owns an independently addressed V5 subprocess bound to a copied DB only."""

    def __init__(self, endpoint: MemoryV5ShadowEndpoint, shadow_db: Path, social_layer_src: Path) -> None:
        self.endpoint = endpoint
        self.shadow_db = shadow_db.resolve()
        self.social_layer_src = social_layer_src.resolve()
        self._process = None  # type: Optional[subprocess.Popen[bytes]]

    @staticmethod
    def unused_local_port() -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1", 0))
            return int(listener.getsockname()[1])

    def start(self, timeout_seconds: float = 8.0) -> None:
        if self._process is not None or not self.shadow_db.is_file() or not self.social_layer_src.is_dir():
            raise RuntimeError("isolated Memory V5 process is not startable")
        parsed = urlparse(self.endpoint.base_url)
        environment = dict(os.environ)
        environment["YOGURT_SOCIAL_DB"] = str(self.shadow_db)
        environment.pop("YOGURT_MEMORY_GATE_EXPERIMENT", None)
        existing_path = environment.get("PYTHONPATH", "")
        environment["PYTHONPATH"] = str(self.social_layer_src) + (os.pathsep + existing_path if existing_path else "")
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        self._process = subprocess.Popen(
            [
                sys.executable, "-m", "uvicorn", "yogurt_social.memory_service.app:create_default_app",
                "--factory", "--host", "127.0.0.1", "--port", str(parsed.port), "--log-level", "warning",
            ],
            cwd=str(self.social_layer_src.parent.parent),
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
        )
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            if self._process.poll() is not None:
                raise RuntimeError("isolated Memory V5 process exited during startup")
            try:
                with urlopen(self.endpoint.base_url + "/v1/health", timeout=0.2) as response:
                    if response.status == 200:
                        return
            except (OSError, URLError):
                time.sleep(0.05)
        self.stop()
        raise RuntimeError("isolated Memory V5 process did not become healthy")

    def stop(self) -> None:
        if self._process is None:
            return
        self._process.terminate()
        try:
            self._process.wait(timeout=3.0)
        except subprocess.TimeoutExpired:
            self._process.kill()
            self._process.wait(timeout=3.0)
        self._process = None
