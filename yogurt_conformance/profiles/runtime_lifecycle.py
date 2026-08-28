from yogurt_contracts.foundation.identifiers import GenerationAuthorityId, NodeId, RuntimeInstanceId, RuntimeSlotId
from yogurt_contracts.runtime.fencing import FencingDecision, compare_generation_numbers, validate_runtime_against_current_assignment
from yogurt_contracts.runtime.identity import RuntimeIdentity
from yogurt_conformance.fakes.generation_authority import GenerationAuthorityHarness
from yogurt_conformance.protocols.adapters import RuntimeLifecycleAdapterProtocol
from ._scenarios import assert_scenarios


def run_runtime_lifecycle_conformance(slot: str, generation: int, active_slot: str, active_generation: int) -> FencingDecision:
    """Legacy numeric ordering profile, intentionally not a security fencing profile."""

    return compare_generation_numbers(slot, generation, active_slot, active_generation)


def run_runtime_lifecycle_adapter_conformance(adapter: RuntimeLifecycleAdapterProtocol) -> None:
    assert_scenarios(adapter, ("identity_stable", "slot_generation_monotonic", "slots_independent", "zombie_rejected", "wrong_instance_rejected", "expired_lease_recognized", "binding_generation_rejected"))


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
