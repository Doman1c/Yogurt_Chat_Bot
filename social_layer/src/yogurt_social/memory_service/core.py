from __future__ import annotations

import re
import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from ..auto_memory import AutoMemoryStore
from ..conversation_state_runtime import ConversationStateRuntimeStore
from ..memory import MemoryStore
from ..memory_gate_experiment import (
    GateExperimentStore,
    evaluate_counterfactual,
    resolve_gate_experiment,
)
from ..memory_recall_gate import MemoryRecallGate, RecallCandidate
from .retrieval import FtsMemoryIndex


_LOW_INFORMATION = {"好", "好的", "嗯", "收到", "继续", "ok", "okay", "在吗", "哈哈", "行", "可以", "明白"}
_CONTROL_SCHEMA = """
CREATE TABLE IF NOT EXISTS memory_observe_requests (
    idempotency_key TEXT NOT NULL,
    operation TEXT NOT NULL,
    canonical_user_id TEXT NOT NULL,
    conversation_key TEXT NOT NULL,
    result_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'completed',
    created_at TEXT NOT NULL,
    PRIMARY KEY(idempotency_key, operation)
);
"""


@dataclass(frozen=True)
class MemoryServiceConfig:
    db_path: Path
    default_persona_id: str = "yogurt"
    promotion_threshold: float = 0.78
    recall_min_score: float = 0.52
    recall_max_items: int = 3
    recall_token_budget: int = 220
    recall_gate_experiment: Optional[str] = None


class MemoryService:
    """V5 owner that wraps, rather than replaces, V3/V4.2 behavior."""

    def __init__(self, config: MemoryServiceConfig) -> None:
        self.config = config
        self.db_path = Path(config.db_path).expanduser().resolve()
        self.memory = MemoryStore(self.db_path)
        self.auto_memory = AutoMemoryStore(self.db_path, config.promotion_threshold)
        self.states = ConversationStateRuntimeStore(self.db_path)
        self.index = FtsMemoryIndex(self.db_path)
        self.gate = MemoryRecallGate(self.db_path, min_score=config.recall_min_score)
        self.gate_experiment_profile = resolve_gate_experiment(config.recall_gate_experiment)
        self.gate_experiment_store: Optional[GateExperimentStore] = None
        self._gate_experiment_degraded_reason: Optional[str] = None
        if self.gate_experiment_profile is not None:
            try:
                self.gate_experiment_store = GateExperimentStore(self.db_path)
            except Exception:
                # An experiment audit must never turn Recall into a service
                # outage. Readiness exposes this degraded state without text.
                self._gate_experiment_degraded_reason = "audit_store_unavailable"
        with closing(self._connect()) as connection:
            connection.executescript(_CONTROL_SCHEMA)
            columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(memory_observe_requests)")
            }
            if "status" not in columns:
                connection.execute(
                    "ALTER TABLE memory_observe_requests "
                    "ADD COLUMN status TEXT NOT NULL DEFAULT 'completed'"
                )
            connection.commit()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA busy_timeout = 3000")
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def canonical_user_id(self, persona_id: str, user_id: str) -> str:
        persona = self._identifier(persona_id, "persona_id")
        user = self._identifier(user_id, "user_id")
        # Existing Yogurt V1/V3 databases already use owner directly. Preserve that
        # key for zero-copy migration; future persona namespaces are explicit.
        return user if persona == self.config.default_persona_id else f"{persona}:{user}"

    @staticmethod
    def scoped_conversation_key(surface: str, conversation_key: str) -> str:
        normalized_surface = MemoryService._identifier(surface, "surface")
        raw_key = MemoryService._identifier(conversation_key, "conversation_key")
        prefix = f"{normalized_surface}:"
        return raw_key if raw_key.startswith(prefix) else f"{prefix}{raw_key}"

    @staticmethod
    def _identifier(value: str, field: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field} must be a non-empty string")
        result = value.strip()
        if len(result) > 180:
            raise ValueError(f"{field} is too long")
        return result

    @staticmethod
    def _pre_gate(message: str) -> str | None:
        compact = re.sub(r"\s+", "", message.casefold()).strip("!！。,.，")
        if not compact or compact in _LOW_INFORMATION:
            return "low_information"
        return None

    @staticmethod
    def _token_estimate(text: str) -> int:
        cjk = len(re.findall(r"[\u3400-\u9fff]", text))
        non_cjk = re.sub(r"[\u3400-\u9fff]", " ", text)
        return cjk + len(re.findall(r"[A-Za-z0-9_]+|[^\s]", non_cjk))

    @staticmethod
    def _idempotency_key(value: str | None) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str) or not value.strip() or len(value.strip()) > 180:
            raise ValueError("idempotency_key must be a non-empty bounded string")
        return value.strip()

    def _load_completed_idempotent(
        self,
        key: str | None,
        operation: str,
        canonical_user: str | None = None,
        scoped_key: str | None = None,
    ) -> dict[str, Any] | None:
        if key is None:
            return None
        query = """
            SELECT result_json FROM memory_observe_requests
            WHERE idempotency_key = ? AND operation = ? AND status = 'completed'
        """
        values: list[str] = [key, operation]
        if canonical_user is not None and scoped_key is not None:
            query += " AND canonical_user_id = ? AND conversation_key = ?"
            values.extend((canonical_user, scoped_key))
        with closing(self._connect()) as connection:
            row = connection.execute(
                query, values,
            ).fetchone()
        if row is None:
            return None
        try:
            value = json.loads(str(row["result_json"]))
        except (TypeError, json.JSONDecodeError):
            return None
        if not isinstance(value, dict):
            return None
        value["idempotent"] = True
        return value

    def _claim_idempotent(
        self,
        key: str | None,
        operation: str,
        canonical_user: str,
        scoped_key: str,
    ) -> tuple[dict[str, Any] | None, bool]:
        if key is None:
            return None, True
        from datetime import datetime, timezone

        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT status, result_json FROM memory_observe_requests
                WHERE idempotency_key = ? AND operation = ?
                  AND canonical_user_id = ? AND conversation_key = ?
                """,
                (key, operation, canonical_user, scoped_key),
            ).fetchone()
            if row is not None:
                connection.commit()
                if str(row["status"]) == "completed":
                    try:
                        result = json.loads(str(row["result_json"]))
                    except (TypeError, json.JSONDecodeError):
                        result = None
                    if isinstance(result, dict):
                        result["idempotent"] = True
                        return result, False
                return {
                    "ok": True, "in_progress": True, "idempotent": True,
                    "canonical_write": False, "index_updated": False,
                    "index_dirty": False,
                }, False
            connection.execute(
                """
                INSERT INTO memory_observe_requests(
                    idempotency_key, operation, canonical_user_id, conversation_key,
                    result_json, status, created_at
                ) VALUES (?, ?, ?, ?, '{}', 'processing', ?)
                """,
                (key, operation, canonical_user, scoped_key, datetime.now(timezone.utc).isoformat()),
            )
            connection.commit()
        return None, True

    def _complete_idempotent(
        self,
        key: str | None,
        operation: str,
        canonical_user: str,
        scoped_key: str,
        result: dict[str, Any],
    ) -> None:
        if key is None:
            return
        stored = {name: value for name, value in result.items() if name != "idempotent"}
        with closing(self._connect()) as connection:
            connection.execute(
                """
                UPDATE memory_observe_requests
                SET status = 'completed', result_json = ?
                WHERE idempotency_key = ? AND operation = ?
                  AND canonical_user_id = ? AND conversation_key = ?
                """,
                (json.dumps(stored, ensure_ascii=False, sort_keys=True), key, operation, canonical_user, scoped_key),
            )
            connection.commit()

    def _record_gate_experiment(
        self,
        *,
        baseline_decision: str,
        baseline_threshold: Optional[float],
        explicit_request: bool,
        high_risk: bool,
        production_reasons: list[str],
        candidate: Optional[RecallCandidate],
        pre_gate: bool = False,
    ) -> None:
        """Best-effort B.7 audit; it has no path back into recall output/state."""
        profile = self.gate_experiment_profile
        store = self.gate_experiment_store
        if profile is None:
            return
        if store is None:
            self._gate_experiment_degraded_reason = "audit_store_unavailable"
            return
        try:
            evaluation = evaluate_counterfactual(
                candidate=candidate,
                baseline_decision=baseline_decision,
                explicit_request=explicit_request,
                high_risk=high_risk,
                production_reasons=production_reasons,
                candidate_base_min_score=profile.candidate_base_min_score,
                pre_gate=pre_gate,
            )
            store.record(
                profile=profile,
                baseline_base_threshold=self.gate.min_score,
                baseline_decision=baseline_decision,
                baseline_threshold=baseline_threshold,
                explicit_request=explicit_request,
                production_reasons=production_reasons,
                candidate=candidate,
                evaluation=evaluation,
            )
            self._gate_experiment_degraded_reason = None
        except Exception:
            self._gate_experiment_degraded_reason = "audit_write_failed"

    def observe(
        self,
        *,
        persona_id: str,
        user_id: str,
        surface: str,
        conversation_key: str,
        phase: str,
        user_message: str = "",
        assistant_reply: str = "",
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        canonical_user = self.canonical_user_id(persona_id, user_id)
        scoped_key = self.scoped_conversation_key(surface, conversation_key)
        if phase not in {"user", "assistant"}:
            raise ValueError("phase must be user or assistant")
        key = self._idempotency_key(idempotency_key)
        operation = f"observe_{phase}"
        existing, claimed = self._claim_idempotent(key, operation, canonical_user, scoped_key)
        if existing is not None:
            return existing
        if not claimed:
            raise RuntimeError("idempotency_claim_failed")
        if phase == "user":
            state = self.states.observe_user_turn(canonical_user, scoped_key, user_message)
            result = {
                "ok": True, "phase": phase, "conversation_key": scoped_key,
                "state_version": state.state_version, "canonical_write": False,
                "index_updated": False, "index_dirty": False, "candidate_count": 0,
                "promoted_count": 0, "conflict_count": 0, "idempotent": False,
            }
        else:
            state = self.states.observe_assistant_turn(canonical_user, scoped_key, assistant_reply)
            writes = [item.to_dict() for item in self.auto_memory.process_chat(
                user_id=canonical_user, user_message=user_message, assistant_reply=assistant_reply,
            )]
            try:
                self.index.rebuild_user(canonical_user_id=canonical_user, persona_id=persona_id)
                index_updated = True
            except Exception:
                # Canonical V3 work has committed. A derived-index outage must
                # neither invalidate this response nor invite a duplicate retry.
                index_updated = False
            result = {
                "ok": True, "phase": phase, "conversation_key": scoped_key,
                "state_version": state.state_version, "canonical_write": True,
                "index_updated": index_updated, "index_dirty": not index_updated,
                "candidate_count": len(writes),
                "promoted_count": sum(item["status"] == "promoted" for item in writes),
                "conflict_count": sum(item["status"] == "conflict" for item in writes),
                "idempotent": False,
            }
        self._complete_idempotent(key, operation, canonical_user, scoped_key, result)
        return result

    def recall(
        self,
        *,
        persona_id: str,
        user_id: str,
        surface: str,
        conversation_key: str,
        message: str,
        max_tokens: int | None = None,
        max_items: int | None = None,
        turn_id: str | None = None,
    ) -> dict[str, Any]:
        canonical_user = self.canonical_user_id(persona_id, user_id)
        scoped_key = self.scoped_conversation_key(surface, conversation_key)
        budget = max(0, min(350, max_tokens if max_tokens is not None else self.config.recall_token_budget))
        del max_items  # V5-A/B is intentionally top-1 only.
        blocked = self._pre_gate(message)
        if blocked:
            self._record_gate_experiment(
                baseline_decision="skip", baseline_threshold=None,
                explicit_request=False, high_risk=False, production_reasons=[],
                candidate=None, pre_gate=True,
            )
            return {"decision": "skip", "memories": [], "token_estimate": 0, "reason": blocked, "mode": "pre_gate"}

        self.index.rebuild_user(canonical_user_id=canonical_user, persona_id=persona_id)
        observed = self._load_completed_idempotent(
            self._idempotency_key(turn_id), "observe_user", canonical_user, scoped_key,
        )
        state = self.states.get(canonical_user, scoped_key) if observed is not None else None
        # Every real recall turn must reflect its current message. A caller that
        # already used /observe phase=user supplies the same turn_id and is not
        # observed twice.
        if state is None:
            state = self.states.observe_user_turn(canonical_user, scoped_key, message)
        explicit = self.gate._explicit_request(message)
        documents = self.index.search(canonical_user_id=canonical_user, message=message, limit=8)
        candidates: list[RecallCandidate] = []
        continuity = [state.current_topic or "", *state.open_threads]
        for document in documents:
            score, lexical, continuity_score, quality = self.gate._score(
                message=message,
                continuity_texts=continuity,
                candidate_text=document.search_text,
                quality=document.quality,
                explicit_request=explicit,
            )
            candidates.append(RecallCandidate(
                "stable" if document.source_kind != "episode" else "episodic",
                document.source_kind, document.source_id, document.memory_key,
                document.prompt_text, score, lexical, continuity_score, quality,
            ))
        candidates.sort(key=lambda item: (item.score, item.lexical_score, item.quality_score), reverse=True)
        decision = self.gate.evaluate(
            user_id=canonical_user,
            conversation_key=scoped_key,
            message=message,
            state=state,
            candidates_override=candidates,
        )
        if decision.decision != "allow" or decision.candidate is None:
            result = {"decision": decision.decision, "memories": [], "token_estimate": 0, "reason": list(decision.reasons), "mode": "shadow_candidate"}
            self._record_gate_experiment(
                baseline_decision=decision.decision, baseline_threshold=decision.threshold,
                explicit_request=decision.explicit_request,
                high_risk=self.gate._high_risk_topic(message),
                production_reasons=list(decision.reasons), candidate=decision.candidate,
            )
            return result
        candidate = decision.candidate
        estimate = self._token_estimate(candidate.text)
        if estimate > budget:
            result = {
                "decision": "blocked", "memories": [], "token_estimate": 0,
                "reason": [*decision.reasons, "token_budget_exceeded"],
                "mode": "budget_blocked",
            }
            self._record_gate_experiment(
                baseline_decision=decision.decision, baseline_threshold=decision.threshold,
                explicit_request=decision.explicit_request,
                high_risk=self.gate._high_risk_topic(message),
                production_reasons=list(decision.reasons), candidate=decision.candidate,
            )
            return result
        result = {
            "decision": "allow",
            "memories": [{
                "kind": candidate.kind, "key": candidate.memory_key,
                "text": candidate.text, "score": round(candidate.score, 4),
                "source_id": candidate.source_id,
            }],
            "token_estimate": estimate,
            "reason": list(decision.reasons), "mode": "gate_allow",
        }
        self._record_gate_experiment(
            baseline_decision=decision.decision, baseline_threshold=decision.threshold,
            explicit_request=decision.explicit_request,
            high_risk=self.gate._high_risk_topic(message),
            production_reasons=list(decision.reasons), candidate=decision.candidate,
        )
        return result

    def snapshot(self, *, persona_id: str, user_id: str) -> dict[str, Any]:
        return self.memory.snapshot(self.canonical_user_id(persona_id, user_id))

    def candidates(self, *, persona_id: str, user_id: str, status: str | None = None) -> list[dict[str, Any]]:
        return self.auto_memory.list_candidates(self.canonical_user_id(persona_id, user_id), status)

    def revisions(self, *, persona_id: str, user_id: str, limit: int = 50) -> list[dict[str, Any]]:
        import sqlite3
        from contextlib import closing
        canonical_user = self.canonical_user_id(persona_id, user_id)
        with closing(sqlite3.connect(self.db_path)) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                "SELECT id, kind, memory_key, action, candidate_id, created_at FROM memory_revisions WHERE user_id = ? ORDER BY id DESC LIMIT ?",
                (canonical_user, max(1, min(500, limit))),
            ).fetchall()
        return [dict(row) for row in rows]

    def readiness(self) -> dict[str, Any]:
        profile = self.gate_experiment_profile
        return {
            "ok": True, "db": "ready", "index": self.index.status(),
            "service": "yogurt-memory-v5",
            "gate_experiment": {
                "enabled": profile is not None,
                "name": profile.name if profile is not None else "off",
                "candidate_base_min_score": (
                    profile.candidate_base_min_score if profile is not None else None
                ),
                "degraded": self._gate_experiment_degraded_reason is not None,
            },
        }
