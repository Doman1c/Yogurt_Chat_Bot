"""Shadow-only reconciliation proof derived from isolated V5 durable state."""

from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

from yogurt_contracts.memory.causal import (
    MemoryCausalResolution,
    MemoryDispatchAttempt,
    MemoryObservationOperation,
    MemoryReconciliationProof,
)


_OBSERVE_CAPABILITY_ID = "memory.observe"

from .adapters import memory_v5_turn_correlation_key
from .shadow import MemoryV5ShadowEndpoint


@dataclass(frozen=True)
class MemoryV5ShadowReconciliation:
    resolution: MemoryCausalResolution
    proof: MemoryReconciliationProof


class MemoryV5ShadowReconciler:
    """Reads only the isolated V5 idempotency ledger; production is never addressable."""

    def __init__(self, endpoint: MemoryV5ShadowEndpoint, shadow_db: Path) -> None:
        self._endpoint = endpoint
        self._shadow_db = shadow_db.resolve()
        if not self._shadow_db.is_file():
            raise ValueError("isolated V5 shadow database must exist")

    async def reconcile(
        self,
        observation: MemoryObservationOperation,
        attempts: Tuple[MemoryDispatchAttempt, ...],
    ) -> Optional[MemoryV5ShadowReconciliation]:
        if observation.logical_provider_domain != self._endpoint.logical_provider_domain:
            raise ValueError("CROSS_DOMAIN_UNCERTAINTY_REJECTED")
        if not attempts:
            raise ValueError("MEMORY_DISPATCH_ATTEMPT_NOT_FOUND")
        latest_attempt = attempts[-1]
        if (
            latest_attempt.logical_provider_domain != observation.logical_provider_domain
            or latest_attempt.logical_provider_domain != self._endpoint.logical_provider_domain
            or latest_attempt.provider_binding.capability_id.value != _OBSERVE_CAPABILITY_ID
            or latest_attempt.dispatch_attempt.operation_id != observation.operation_id
        ):
            # Completion evidence is untrusted until the durable attempt proves
            # that it was an observe call for this semantic observation/domain.
            raise ValueError("MEMORY_DISPATCH_ATTEMPT_PROVENANCE_MISMATCH")
        return await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: self._read_completed_observation(observation, latest_attempt),
        )

    def _read_completed_observation(
        self,
        observation: MemoryObservationOperation,
        latest_attempt: MemoryDispatchAttempt,
    ) -> Optional[MemoryV5ShadowReconciliation]:
        key = memory_v5_turn_correlation_key(observation)
        canonical_user = observation.observation.conversation_ref.user_id.value
        if observation.observation.conversation_ref.persona_id.value != "yogurt":
            canonical_user = observation.observation.conversation_ref.persona_id.value + ":" + canonical_user
        scoped_key = "r3c-shadow:" + observation.scope_ref.scope_id.value
        uri = "file:" + self._shadow_db.as_posix() + "?mode=ro"
        with sqlite3.connect(uri, uri=True) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                "SELECT status, result_json FROM memory_observe_requests "
                "WHERE idempotency_key = ? AND operation = 'observe_user' "
                "AND canonical_user_id = ? AND conversation_key = ?",
                (key, canonical_user, scoped_key),
            ).fetchone()
        if row is None or str(row["status"]) != "completed":
            # A missing or processing V5 row is not proof of no effect.
            return None
        try:
            result = json.loads(str(row["result_json"]))
        except (TypeError, json.JSONDecodeError):
            return None
        if not isinstance(result, dict) or result.get("ok") is not True or result.get("in_progress") is True:
            return None
        reference = "v5-completed-" + hashlib.sha256(key.encode("utf-8")).hexdigest()
        proof = MemoryReconciliationProof(
            operation_id=observation.operation_id,
            logical_provider_domain=self._endpoint.logical_provider_domain,
            runtime_instance_id=self._endpoint.runtime_instance_id,
            runtime_generation=self._endpoint.runtime_generation,
            binding_id=latest_attempt.provider_binding.binding_id,
            resolution_reference=reference,
        )
        return MemoryV5ShadowReconciliation(MemoryCausalResolution.APPLIED, proof)
