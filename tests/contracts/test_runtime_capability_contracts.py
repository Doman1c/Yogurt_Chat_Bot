from datetime import datetime, timezone

from yogurt_contracts.capability.framework import BaseCapabilityDescriptor, CapabilityState
from yogurt_contracts.capability.offer import CapabilityOffer
from yogurt_contracts.foundation.identifiers import CapabilityId, HypothesisId, NodeId, RuntimeInstanceId, RuntimeSlotId
from yogurt_contracts.foundation.time import UtcInstant
from yogurt_contracts.foundation.versioning import ContractVersion
from yogurt_contracts.runtime.fencing import FencingDecision, validate_generation
from yogurt_contracts.runtime.identity import RuntimeIdentity
from yogurt_contracts.streams.transcript import TranscriptHypothesis, supersedes


class ExampleExternalDescriptor(BaseCapabilityDescriptor):
    supports_demo: bool


NOW = UtcInstant(value=datetime(2026, 1, 1, tzinfo=timezone.utc))


def test_external_typed_capability_descriptor_needs_no_central_union():
    offer = CapabilityOffer[ExampleExternalDescriptor](capability_id=CapabilityId(value="example.external"), contract_version=ContractVersion(major=1, minor=0), provider_slot_id=RuntimeSlotId(value="slot"), provider_instance_id=RuntimeInstanceId(value="instance"), provider_generation=2, descriptor=ExampleExternalDescriptor(supports_demo=True), state=CapabilityState.AVAILABLE, lease_expires_at=NOW)
    assert offer.descriptor.supports_demo


def test_generation_is_scoped_by_slot():
    assert validate_generation("voice.mac", 5, "voice.mac", 6) == FencingDecision.STALE_GENERATION
    assert validate_generation("voice.stackchan", 2, "voice.mac", 6) == FencingDecision.SLOT_MISMATCH


def test_transcript_is_revision_replacement_not_append():
    old = TranscriptHypothesis(hypothesis_id=HypothesisId(value="hypothesis"), revision=1, text="I want to eat")
    new = TranscriptHypothesis(hypothesis_id=HypothesisId(value="hypothesis"), revision=2, text="I want to go", is_final=True)
    assert supersedes(new, old)
