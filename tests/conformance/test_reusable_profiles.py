import pytest

from yogurt_conformance.fakes.conformance_adapters import CommitMeansSeenMemoryFake, DispatchWithoutIntentFake, DraftSpeakingVoiceFake, DuplicateTurnExecutionFake, ForgedReadReceiptSurface, GoodCapabilityProviderFake, GoodCorePublicFake, GoodMemoryFake, GoodOperationProviderFake, GoodRuntimeLifecycleFake, GoodSurfaceFake, GoodVoiceFake
from yogurt_conformance.profiles.capability_provider import run_capability_provider_conformance
from yogurt_conformance.profiles.core_public import run_core_public_conformance
from yogurt_conformance.profiles.memory import run_memory_conformance
from yogurt_conformance.profiles.operation_provider import run_operation_provider_conformance
from yogurt_conformance.profiles.runtime_lifecycle import run_runtime_lifecycle_conformance
from yogurt_conformance.profiles.surface import run_surface_conformance
from yogurt_conformance.profiles.voice import run_voice_conformance


def test_good_behavioral_adapters_pass_all_reusable_profiles() -> None:
    run_core_public_conformance(GoodCorePublicFake())
    run_memory_conformance(GoodMemoryFake())
    run_surface_conformance(GoodSurfaceFake())
    run_voice_conformance(GoodVoiceFake())
    run_operation_provider_conformance(GoodOperationProviderFake())
    run_capability_provider_conformance(GoodCapabilityProviderFake())
    run_runtime_lifecycle_conformance(GoodRuntimeLifecycleFake())


@pytest.mark.parametrize(("profile", "adapter"), ((run_core_public_conformance, DuplicateTurnExecutionFake()), (run_memory_conformance, CommitMeansSeenMemoryFake()), (run_surface_conformance, ForgedReadReceiptSurface()), (run_voice_conformance, DraftSpeakingVoiceFake()), (run_operation_provider_conformance, DispatchWithoutIntentFake())))
def test_bad_adapters_fail_the_profile_that_exercises_them(profile, adapter) -> None:
    with pytest.raises(AssertionError):
        profile(adapter)
