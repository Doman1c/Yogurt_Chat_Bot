"""R2-A additions must not hide the established R1 public contract surface."""


def test_execution_exports_include_r1_and_r2a_contracts():
    from yogurt_contracts.execution import ExecutionAttemptSnapshot, OperationAuthorizationSnapshot, PersonaSnapshot, TurnAdmission, TurnSnapshot

    assert all((ExecutionAttemptSnapshot, OperationAuthorizationSnapshot, PersonaSnapshot, TurnAdmission, TurnSnapshot))


def test_response_exports_include_legacy_and_event_contracts():
    from yogurt_contracts.response import AgentOutputSegmentCommitted, AgentResponseAborted, AttemptDisposition, ExecutionAttempt, LogicalResponse, ResponseAbortedEvent, ResponseEvent, ResponseState

    assert all((AgentOutputSegmentCommitted, AgentResponseAborted, AttemptDisposition, ExecutionAttempt, LogicalResponse, ResponseAbortedEvent, ResponseEvent, ResponseState))


def test_runtime_exports_include_legacy_and_core_authority_contracts():
    from yogurt_contracts.runtime import CoreAuthorityToken, RuntimeIdentity, RuntimeLease, RuntimeManifest, validate_bound_provider

    assert all((CoreAuthorityToken, RuntimeIdentity, RuntimeLease, RuntimeManifest, validate_bound_provider))
