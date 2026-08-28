"""Conformance tests prove both a durable authority and a deliberately bad authority."""

import pytest

from yogurt_contracts.foundation.identifiers import GenerationAuthorityId, RuntimeInstanceId, RuntimeSlotId
from yogurt_conformance.fakes.generation_authority import FakeGenerationAuthority, ResettingGenerationAuthorityFake
from yogurt_conformance.profiles.runtime_lifecycle import run_generation_authority_conformance


AUTHORITY = GenerationAuthorityId(value="generation-authority")


def test_fake_generation_authority_is_monotonic_per_slot_and_recovers_after_restart() -> None:
    authority = FakeGenerationAuthority(AUTHORITY)
    run_generation_authority_conformance(authority, AUTHORITY)


def test_different_slots_have_independent_lineages_and_new_instances_advance_the_slot() -> None:
    authority = FakeGenerationAuthority(AUTHORITY)
    first = authority.issue(RuntimeSlotId(value="voice.mac"), RuntimeInstanceId(value="first"))
    second = authority.issue(RuntimeSlotId(value="voice.mac"), RuntimeInstanceId(value="second"))
    other = authority.issue(RuntimeSlotId(value="voice.stackchan"), RuntimeInstanceId(value="other"))
    assert (first.generation, second.generation, other.generation) == (1, 2, 1)


def test_resetting_generation_authority_fails_durable_restart_conformance() -> None:
    with pytest.raises(AssertionError):
        run_generation_authority_conformance(ResettingGenerationAuthorityFake(AUTHORITY), AUTHORITY)
