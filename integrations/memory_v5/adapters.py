"""Transport and legacy-schema translation for an isolated Memory V5 process."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from yogurt_contracts.capability.binding import CapabilityBinding
from yogurt_contracts.execution import CapabilityAuthorizationSnapshot
from yogurt_contracts.foundation.identifiers import CapabilityId
from yogurt_contracts.foundation.idempotency import RequestFingerprint
from yogurt_contracts.foundation.time import UtcInstant
from yogurt_contracts.memory.causal import MemoryObservationOperation
from yogurt_contracts.memory.recall import (
    MEMORY_RECALL_CAPABILITY_ID,
    MemoryProviderMetadata,
    MemoryRecallBindingSnapshot,
    MemoryRecallCallOutcome,
    MemoryRecallCandidate,
    MemoryRecallDecision,
    MemoryRecallEvidence,
    MemoryRecallProviderResult,
    MemoryRecallQuery,
    MemoryRecallCapabilityState,
    normalize_memory_text,
)
from yogurt_contracts.operations.intent import OperationIntentCommitted
from yogurt_core.operations.models import ProviderDispatchCertainty, ProviderDispatchResult

from .shadow import MemoryV5ShadowEndpoint


_OBSERVE_CAPABILITY = CapabilityId(value="memory.observe")


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def memory_v5_turn_correlation_key(observation: MemoryObservationOperation) -> str:
    """The one V5 key shared by user-observe idempotency and later recall."""

    return "r3c-v5-turn-" + _digest(
        observation.operation_id.value + ":" + observation.observation_fingerprint.value
    )


def _post_json(base_url: str, path: str, payload: Dict[str, Any], timeout_seconds: float) -> Tuple[int, Optional[Dict[str, Any]]]:
    request = Request(
        base_url + path,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            decoded = json.loads(response.read().decode("utf-8"))
            return int(response.status), decoded if isinstance(decoded, dict) else None
    except HTTPError as error:
        try:
            decoded = json.loads(error.read().decode("utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            decoded = None
        return int(error.code), decoded if isinstance(decoded, dict) else None
    except (OSError, URLError, ValueError, json.JSONDecodeError):
        return 0, None


def _get_json(base_url: str, path: str, timeout_seconds: float) -> Tuple[int, Optional[Dict[str, Any]]]:
    try:
        with urlopen(base_url + path, timeout=timeout_seconds) as response:
            decoded = json.loads(response.read().decode("utf-8"))
            return int(response.status), decoded if isinstance(decoded, dict) else None
    except (OSError, URLError, ValueError, json.JSONDecodeError):
        return 0, None


async def _in_executor(callable_object: Any) -> Any:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, callable_object)


class MemoryV5RecallAdapter:
    """MemoryRecallPort implementation whose HTTP and V5 quirks stay out of Core."""

    def __init__(
        self,
        endpoint: MemoryV5ShadowEndpoint,
        binding: MemoryRecallBindingSnapshot,
        current_observation: MemoryObservationOperation,
        timeout_seconds: float = 0.8,
    ) -> None:
        if (
            binding.logical_provider_id != endpoint.logical_provider_domain
            or binding.runtime_instance_id != endpoint.runtime_instance_id
            or binding.runtime_generation != endpoint.runtime_generation
            or binding.capability_id.value != MEMORY_RECALL_CAPABILITY_ID
        ):
            raise ValueError("Memory V5 recall binding must match its isolated runtime")
        if current_observation.logical_provider_domain != endpoint.logical_provider_domain:
            raise ValueError("Memory V5 recall observation must match isolated runtime domain")
        self._endpoint = endpoint
        self._binding = binding
        self._current_observation = current_observation
        self._timeout_seconds = timeout_seconds

    async def capability_state(self) -> MemoryRecallCapabilityState:
        status, body = await _in_executor(
            lambda: _get_json(self._endpoint.base_url, "/v1/ready", self._timeout_seconds)
        )
        if status == 200 and body is not None and body.get("ok") is True:
            return MemoryRecallCapabilityState.AVAILABLE
        return MemoryRecallCapabilityState.UNAVAILABLE

    async def current_binding(self) -> Optional[MemoryRecallBindingSnapshot]:
        return self._binding if await self.capability_state() == MemoryRecallCapabilityState.AVAILABLE else None

    async def recall(
        self,
        query: MemoryRecallQuery,
        binding: MemoryRecallBindingSnapshot,
        authorization: CapabilityAuthorizationSnapshot,
    ) -> MemoryRecallProviderResult:
        del authorization
        if (
            binding != self._binding
            or query.current_input_fingerprint != self._current_observation.observation_fingerprint
            or query.turn_id != self._current_observation.observation.turn_ref.turn_id
            or query.memory_conversation_scope_ref != self._current_observation.scope_ref
        ):
            return MemoryRecallProviderResult(call_outcome=MemoryRecallCallOutcome.UNAVAILABLE)
        remaining_seconds = (query.deadline.expires_at.value - datetime.now(timezone.utc)).total_seconds()
        if remaining_seconds <= 0:
            return MemoryRecallProviderResult(call_outcome=MemoryRecallCallOutcome.DEADLINE_EXCEEDED)
        timeout_seconds = min(self._timeout_seconds, remaining_seconds)
        payload = {
            "persona_id": query.conversation_ref.persona_id.value,
            "user_id": query.conversation_ref.user_id.value,
            "surface": "r3c-shadow",
            "conversation_key": query.memory_conversation_scope_ref.scope_id.value,
            "message": query.normalized_user_message,
            "max_tokens": query.token_budget,
            "max_items": query.max_items,
            "turn_id": memory_v5_turn_correlation_key(self._current_observation),
        }
        status, body = await _in_executor(
            lambda: _post_json(self._endpoint.base_url, "/v1/memory/recall", payload, timeout_seconds)
        )
        if status == 0:
            if datetime.now(timezone.utc) >= query.deadline.expires_at.value:
                return MemoryRecallProviderResult(call_outcome=MemoryRecallCallOutcome.DEADLINE_EXCEEDED)
            return MemoryRecallProviderResult(call_outcome=MemoryRecallCallOutcome.UNAVAILABLE)
        if status != 200 or body is None:
            return MemoryRecallProviderResult(call_outcome=MemoryRecallCallOutcome.INVALID_PROVIDER_RESPONSE)
        try:
            decision, candidates = self._translate_recall(body, query)
            evidence = MemoryRecallEvidence(
                recall_query_id=query.recall_query_id,
                conversation_ref=query.conversation_ref,
                turn_id=query.turn_id,
                query_fingerprint=query.query_fingerprint,
                memory_conversation_scope_ref=query.memory_conversation_scope_ref,
                logical_provider_id=self._binding.logical_provider_id,
                memory_domain_id=self._binding.memory_domain_id,
                provider_binding_ref=self._binding,
                produced_by_generation=self._binding.runtime_generation,
                retrieved_at=UtcInstant(value=datetime.now(timezone.utc)),
                decision=decision,
                candidates=candidates,
                advisory_token_estimate=int(body.get("token_estimate", 0)),
                bounded_provider_metadata=(MemoryProviderMetadata(key="v5_mode", value=str(body.get("mode", "unknown"))),),
            )
        except (TypeError, ValueError):
            return MemoryRecallProviderResult(call_outcome=MemoryRecallCallOutcome.INVALID_PROVIDER_RESPONSE)
        return MemoryRecallProviderResult(call_outcome=MemoryRecallCallOutcome.SUCCESS, evidence=evidence)

    @staticmethod
    def _translate_recall(
        payload: Dict[str, Any], query: MemoryRecallQuery,
    ) -> Tuple[MemoryRecallDecision, Tuple[MemoryRecallCandidate, ...]]:
        raw_decision = str(payload.get("decision", ""))
        if raw_decision == "allow":
            raw_memories = payload.get("memories")
            if not isinstance(raw_memories, list) or not raw_memories:
                raise ValueError("allow recall requires memory candidates")
            if len(raw_memories) > query.max_items:
                raise ValueError("V5 returned more candidates than requested")
            candidates = []  # type: list[MemoryRecallCandidate]
            for index, raw in enumerate(raw_memories):
                if not isinstance(raw, dict) or not isinstance(raw.get("text"), str):
                    raise ValueError("invalid V5 memory candidate")
                text = normalize_memory_text(raw["text"])
                if not text:
                    raise ValueError("empty V5 memory candidate")
                source = str(raw.get("source_id", "candidate-" + str(index)))
                candidates.append(MemoryRecallCandidate(
                    stable_candidate_id="v5-" + _digest(source),
                    kind=str(raw.get("kind", "memory")),
                    normalized_text=text,
                    source_ref="v5-source-" + _digest(source),
                    content_fingerprint=RequestFingerprint(value=_digest(text)),
                    advisory_score=float(raw.get("score", 0.0)),
                    content_size=len(text),
                ))
            return MemoryRecallDecision.AVAILABLE, tuple(candidates)
        if raw_decision == "skip":
            return MemoryRecallDecision.SKIPPED, ()
        if raw_decision == "blocked":
            return MemoryRecallDecision.BLOCKED, ()
        return MemoryRecallDecision.EMPTY, ()


class MemoryV5ObservationProvider:
    """Operation provider for one semantic user observation against an isolated V5 runtime."""

    capability_id = _OBSERVE_CAPABILITY

    def __init__(
        self,
        endpoint: MemoryV5ShadowEndpoint,
        observation: MemoryObservationOperation,
        timeout_seconds: float = 0.8,
    ) -> None:
        if observation.logical_provider_domain != endpoint.logical_provider_domain:
            raise ValueError("Memory observation domain must match isolated runtime")
        self._endpoint = endpoint
        self._observation = observation
        self._timeout_seconds = timeout_seconds

    async def dispatch(self, intent: OperationIntentCommitted) -> ProviderDispatchResult:
        try:
            self._observation.validate_durable_intent(intent)
        except ValueError:
            return ProviderDispatchResult(certainty=ProviderDispatchCertainty.DEFINITE_FAILURE)
        binding = intent.provider_binding
        if (
            intent.operation.target_capability != _OBSERVE_CAPABILITY
            or binding.capability_id != _OBSERVE_CAPABILITY
            or binding.provider_instance_id != self._endpoint.runtime_instance_id
            or binding.provider_generation != self._endpoint.runtime_generation
            or self._observation.logical_provider_domain != self._endpoint.logical_provider_domain
        ):
            return ProviderDispatchResult(certainty=ProviderDispatchCertainty.DEFINITE_FAILURE)
        payload = {
            "persona_id": self._observation.observation.conversation_ref.persona_id.value,
            "user_id": self._observation.observation.conversation_ref.user_id.value,
            "surface": "r3c-shadow",
            "conversation_key": self._observation.scope_ref.scope_id.value,
            "phase": "user",
            "user_message": self._observation.observation.content,
            "idempotency_key": memory_v5_turn_correlation_key(self._observation),
        }
        status, body = await _in_executor(
            lambda: _post_json(self._endpoint.base_url, "/v1/memory/observe", payload, self._timeout_seconds)
        )
        if status == 0 or status >= 500:
            return ProviderDispatchResult(certainty=ProviderDispatchCertainty.OUTCOME_UNKNOWN)
        if status != 200 or body is None or body.get("ok") is not True:
            return ProviderDispatchResult(certainty=ProviderDispatchCertainty.DEFINITE_FAILURE)
        if body.get("in_progress") is True:
            return ProviderDispatchResult(certainty=ProviderDispatchCertainty.OUTCOME_UNKNOWN)
        return ProviderDispatchResult(
            certainty=ProviderDispatchCertainty.SUCCESS,
            provider_reference="v5-observe-" + _digest(self._observation.operation_id.value),
        )
