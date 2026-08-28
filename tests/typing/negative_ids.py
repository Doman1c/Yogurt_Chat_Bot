from yogurt_contracts.foundation.identifiers import BindingId, CapabilityId, HypothesisId, LeaseId, OperationAttemptId, OperationId, PersonaId, ReceiptId, RuntimeInstanceId, RuntimeSlotId, ResponseId, SegmentId, TraceId, TurnId, UserId


def needs_operation(value: OperationId) -> None: pass
def needs_response(value: ResponseId) -> None: pass
def needs_slot(value: RuntimeSlotId) -> None: pass
def needs_segment(value: SegmentId) -> None: pass
def needs_attempt(value: OperationAttemptId) -> None: pass
def needs_lease(value: LeaseId) -> None: pass
def needs_persona(value: PersonaId) -> None: pass
def needs_capability(value: CapabilityId) -> None: pass


needs_operation(TraceId(value="trace"))  # E: Argument 1 to "needs_operation" has incompatible type "TraceId"
needs_response(TurnId(value="turn"))  # E: Argument 1 to "needs_response" has incompatible type "TurnId"
needs_operation(OperationAttemptId(value="attempt"))  # E: Argument 1 to "needs_operation" has incompatible type "OperationAttemptId"
needs_slot(RuntimeInstanceId(value="instance"))  # E: Argument 1 to "needs_slot" has incompatible type "RuntimeInstanceId"
needs_segment(ResponseId(value="response"))  # E: Argument 1 to "needs_segment" has incompatible type "ResponseId"
needs_attempt(OperationId(value="operation"))  # E: Argument 1 to "needs_attempt" has incompatible type "OperationId"
needs_lease(BindingId(value="binding"))  # E: Argument 1 to "needs_lease" has incompatible type "BindingId"
needs_persona(UserId(value="user"))  # E: Argument 1 to "needs_persona" has incompatible type "UserId"
needs_operation(ReceiptId(value="receipt"))  # E: Argument 1 to "needs_operation" has incompatible type "ReceiptId"
needs_capability(HypothesisId(value="hypothesis"))  # E: Argument 1 to "needs_capability" has incompatible type "HypothesisId"
