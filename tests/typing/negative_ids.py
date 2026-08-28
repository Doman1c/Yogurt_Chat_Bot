from yogurt_contracts.foundation.identifiers import OperationAttemptId, OperationId, RuntimeInstanceId, RuntimeSlotId, ResponseId, TraceId, TurnId


def needs_operation(value: OperationId) -> None: pass
def needs_response(value: ResponseId) -> None: pass
def needs_slot(value: RuntimeSlotId) -> None: pass


needs_operation(TraceId(value="trace"))  # E: Argument 1 to "needs_operation" has incompatible type "TraceId"
needs_response(TurnId(value="turn"))  # E: Argument 1 to "needs_response" has incompatible type "TurnId"
needs_operation(OperationAttemptId(value="attempt"))  # E: Argument 1 to "needs_operation" has incompatible type "OperationAttemptId"
needs_slot(RuntimeInstanceId(value="instance"))  # E: Argument 1 to "needs_slot" has incompatible type "RuntimeInstanceId"
