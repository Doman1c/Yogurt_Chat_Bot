from yogurt_contracts.foundation.identifiers import GenerationAuthorityId, NodeId, RuntimeInstanceId, RuntimeSlotId
from yogurt_contracts.runtime.fencing import FencingDecision, validate_runtime_against_current_assignment
from yogurt_contracts.runtime.identity import RuntimeIdentity
from yogurt_conformance.fakes.generation_authority import GenerationAuthorityHarness
from yogurt_conformance.protocols.adapters import RuntimeLifecycleAdapterProtocol

def run_runtime_lifecycle_conformance(adapter: RuntimeLifecycleAdapterProtocol) -> None:
    first = adapter.start("slot-a", "instance-a")
    second = adapter.start("slot-a", "instance-b")
    other = adapter.start("slot-b", "instance-c")
    assert first.runtime_slot_id == second.runtime_slot_id and second.runtime_generation == first.runtime_generation + 1
    assert other.runtime_generation == 1 and other.runtime_slot_id != second.runtime_slot_id
    current = adapter.current_assignment("slot-a")
    assert current is not None and current.runtime_instance_id == second.runtime_instance_id and current.generation == second.runtime_generation
    assert not adapter.lease_expired("slot-a") and adapter.lease_expired("missing")


def run_generation_authority_conformance(authority: GenerationAuthorityHarness, expected_authority: GenerationAuthorityId) -> None:
    """Assert durable per-slot monotonic issuance and exact-assignment zombie rejection."""

    voice_slot = RuntimeSlotId(value="voice.mac")
    other_slot = RuntimeSlotId(value="voice.stackchan")
    first = authority.issue(voice_slot, RuntimeInstanceId(value="instance-a"))
    second = authority.issue(voice_slot, RuntimeInstanceId(value="instance-b"))
    other = authority.issue(other_slot, RuntimeInstanceId(value="instance-c"))
    assert first.generation == 1
    assert second.generation == first.generation + 1
    assert other.generation == 1
    snapshot = authority.snapshot_state()
    authority.restore_state(snapshot)
    assert authority.current_assignment(voice_slot) == second
    after_restart = authority.issue(voice_slot, RuntimeInstanceId(value="instance-d"))
    assert after_restart.generation == second.generation + 1
    matching_runtime = RuntimeIdentity(runtime_type="voice", runtime_slot_id=after_restart.runtime_slot_id, runtime_instance_id=after_restart.runtime_instance_id, runtime_generation=after_restart.generation, node_id=NodeId(value="node"))
    assert authority.current_assignment(voice_slot) == after_restart
    assert validate_runtime_against_current_assignment(matching_runtime, after_restart, expected_authority) == FencingDecision.ACCEPT
    zombie_runtime = RuntimeIdentity(runtime_type="voice", runtime_slot_id=voice_slot, runtime_instance_id=RuntimeInstanceId(value="instance-old"), runtime_generation=999, node_id=NodeId(value="node"))
    assert validate_runtime_against_current_assignment(zombie_runtime, after_restart, expected_authority) != FencingDecision.ACCEPT
