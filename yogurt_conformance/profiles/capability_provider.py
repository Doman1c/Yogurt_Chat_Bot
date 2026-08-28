"""Behavioral capability compatibility and binding sequence."""

from datetime import datetime, timezone

from yogurt_contracts.capability.framework import BaseCapabilityDescriptor
from yogurt_contracts.capability.offer import CapabilityOffer
from yogurt_contracts.capability.framework import CapabilityState
from yogurt_contracts.foundation.identifiers import CapabilityId, RuntimeInstanceId, RuntimeSlotId
from yogurt_contracts.foundation.time import UtcInstant
from yogurt_contracts.foundation.versioning import ContractVersion
from yogurt_conformance.protocols.adapters import CapabilityProviderAdapterProtocol


class ExampleExternalDescriptor(BaseCapabilityDescriptor):
    endpoint_kind: str = "external-test"


def run_capability_provider_conformance(adapter: CapabilityProviderAdapterProtocol) -> None:
    offer: CapabilityOffer[BaseCapabilityDescriptor] = CapabilityOffer(capability_id=CapabilityId(value="external"), contract_version=ContractVersion(major=1, minor=4), provider_slot_id=RuntimeSlotId(value="slot"), provider_instance_id=RuntimeInstanceId(value="instance"), provider_generation=1, descriptor=ExampleExternalDescriptor(), state=CapabilityState.AVAILABLE, lease_expires_at=UtcInstant(value=datetime(2026, 1, 2, tzinfo=timezone.utc)))
    adapter.publish_offer(offer)
    assert adapter.bind(1, 2)
    assert not adapter.bind(1, 5)
    assert not adapter.bind(2, 0)
    assert not adapter.bind(1, 2, generation_current=False)
    assert not adapter.bind(1, 2, binding_expired=True)
