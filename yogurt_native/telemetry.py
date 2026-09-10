"""Bounded monotonic timing only; raw content and identity never enter records."""
import hashlib
import time
from collections import OrderedDict
from typing import Dict, Union
from yogurt_contracts.conversation.refs import ConversationRef
from yogurt_contracts.foundation.identifiers import TurnId

CHECKPOINTS = ("surface_received", "core_admission_started", "core_admitted", "model_started",
               "first_model_output", "model_finished", "response_terminal")

class LatencyTelemetry:
    def __init__(self, capacity: int = 1024) -> None:
        if capacity < 1:
            raise ValueError("capacity must be positive")
        self.capacity = capacity
        self.records: OrderedDict[str, Dict[str, float]] = OrderedDict()

    def received(self, conversation: ConversationRef, turn_id: TurnId, instant: float) -> None:
        self.checkpoint(conversation, turn_id, "surface_received")
        key = hashlib.sha256(turn_id.value.encode()).hexdigest()
        row = self.records[key]
        row["surface_received"] = min(row["surface_received"], instant)

    def checkpoint(self, conversation: ConversationRef, turn_id: TurnId, name: str) -> None:
        if name not in CHECKPOINTS:
            return
        key = hashlib.sha256(turn_id.value.encode()).hexdigest()
        if key not in self.records:
            self.records[key] = {}
        record = self.records[key]
        if name == "model_finished" or name not in record:
            record[name] = time.monotonic()
        while len(self.records) > self.capacity:
            self.records.popitem(last=False)

    def summaries(self) -> tuple[Dict[str, Union[str, float, None]], ...]:
        pairs = {"surface_to_core_ms": ("surface_received", "core_admission_started"),
                 "core_pre_model_ms": ("core_admission_started", "model_started"),
                 "model_ttft_ms": ("model_started", "first_model_output"),
                 "model_generation_ms": ("model_started", "model_finished"),
                 "total_ms": ("surface_received", "response_terminal")}
        return tuple({"turn_correlation": correlation, **{name: min(86400000.0, max(0.0, (row[end] - row[start]) * 1000))
                      if start in row and end in row else None
                      for name, (start, end) in pairs.items()}} for correlation, row in self.records.items())
