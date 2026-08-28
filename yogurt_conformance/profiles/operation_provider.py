from yogurt_contracts.foundation.identifiers import OperationId
from yogurt_conformance.protocols.adapters import OperationProviderAdapterProtocol


def run_operation_provider_conformance(adapter: OperationProviderAdapterProtocol) -> None:
    operation = OperationId(value="operation")
    try: adapter.dispatch(operation)
    except (KeyError, ValueError): pass
    else: raise AssertionError("dispatch requires committed intent")
    committed = adapter.commit_intent(operation, "fingerprint")
    assert committed.operation_id == operation and committed.side_effect_count == 0
    assert adapter.commit_intent(operation, "fingerprint").operation_id == operation
    try: adapter.commit_intent(operation, "changed")
    except ValueError: pass
    else: raise AssertionError("changed operation fingerprint must conflict")
    first = adapter.dispatch(operation)
    second = adapter.dispatch(operation)
    assert len(first.attempts) == 1 and len(second.attempts) == 2 and first.attempts[0] != second.attempts[1]
    assert second.side_effect_count == 1
    assert adapter.dispatch(operation, authorization_valid=False).side_effect_count == 1
    assert adapter.dispatch(operation, generation_current=False).side_effect_count == 1
    unknown = adapter.unknown_outcome(operation)
    assert unknown.operation_id == operation and unknown.outcome_unknown
