from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..auto_memory import AutoMemoryStore
from ..conversation_state_runtime import ConversationStateRuntimeStore
from ..memory import MemoryStore
from ..memory_recall_gate import MemoryRecallGate, RecallCandidate
from .retrieval import FtsMemoryIndex


_LOW_INFORMATION = {"好", "好的", "嗯", "收到", "继续", "ok", "okay", "在吗", "哈哈", "行", "可以", "明白"}


@dataclass(frozen=True)
class MemoryServiceConfig:
    db_path: Path
    default_persona_id: str = "yogurt"
    promotion_threshold: float = 0.78
    recall_min_score: float = 0.52
    recall_max_items: int = 3
    recall_token_budget: int = 220


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
    ) -> dict[str, Any]:
        canonical_user = self.canonical_user_id(persona_id, user_id)
        scoped_key = self.scoped_conversation_key(surface, conversation_key)
        if phase not in {"user", "assistant"}:
            raise ValueError("phase must be user or assistant")
        if phase == "user":
            state = self.states.observe_user_turn(canonical_user, scoped_key, user_message)
            writes: list[dict[str, Any]] = []
        else:
            state = self.states.observe_assistant_turn(canonical_user, scoped_key, assistant_reply)
            writes = [item.to_dict() for item in self.auto_memory.process_chat(
                user_id=canonical_user, user_message=user_message, assistant_reply=assistant_reply,
            )]
            self.index.rebuild_user(canonical_user_id=canonical_user, persona_id=persona_id)
        return {
            "ok": True,
            "phase": phase,
            "conversation_key": scoped_key,
            "state_version": state.state_version,
            "candidate_count": len(writes),
            "promoted_count": sum(item["status"] == "promoted" for item in writes),
            "conflict_count": sum(item["status"] == "conflict" for item in writes),
            "writes": writes,
        }

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
    ) -> dict[str, Any]:
        canonical_user = self.canonical_user_id(persona_id, user_id)
        scoped_key = self.scoped_conversation_key(surface, conversation_key)
        budget = max(0, min(350, max_tokens if max_tokens is not None else self.config.recall_token_budget))
        items_limit = max(1, min(3, max_items if max_items is not None else self.config.recall_max_items))
        blocked = self._pre_gate(message)
        if blocked:
            return {"decision": "skip", "memories": [], "token_estimate": 0, "reason": blocked, "mode": "pre_gate"}

        self.index.rebuild_user(canonical_user_id=canonical_user, persona_id=persona_id)
        state = self.states.get(canonical_user, scoped_key)
        if state is None:
            # A direct API caller can recall safely without a prior observe request.
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
            # RRF is intentionally only a candidate-generation tiebreaker; it
            # never replaces the deterministic gate's relevance threshold.
            score = min(1.0, score + (0.015 / max(1, document.rrf_rank)))
            # An FTS/exact entity hit (for example ``显卡`` in the derived GPU
            # aliases) is stronger evidence than the old all-token overlap
            # score, which penalizes naturally long Chinese questions.
            query_tokens = MemoryStore._tokens(message)
            entity_tokens = MemoryStore._tokens(
                f"{document.memory_key} {document.search_text}"
            )
            if query_tokens & entity_tokens:
                score = max(score, 0.60)
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
            return {"decision": decision.decision, "memories": [], "token_estimate": 0, "reason": list(decision.reasons), "mode": "shadow_candidate"}
        chosen: list[dict[str, Any]] = []
        used = 0
        for candidate in candidates[:items_limit]:
            estimate = self._token_estimate(candidate.text)
            if used + estimate > budget:
                continue
            chosen.append({"kind": candidate.kind, "key": candidate.memory_key, "text": candidate.text, "score": round(candidate.score, 4), "source_id": candidate.source_id})
            used += estimate
        # Gate selects the best candidate; do not inject an un-gated tail.
        chosen = chosen[:1]
        return {"decision": "allow", "memories": chosen, "token_estimate": used if chosen else 0, "reason": list(decision.reasons), "mode": "gate_allow"}

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
        return {"ok": True, "db": "ready", "index": self.index.status(), "service": "yogurt-memory-v5"}
