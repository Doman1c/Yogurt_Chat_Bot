from .adapters import FakeOperationProvider, FakeSurfaceAdapter
from .generation_authority import FakeGenerationAuthority, GenerationAuthorityHarness, ResettingGenerationAuthorityFake
from .conformance_adapters import (
    CommitMeansSeenMemoryFake,
    CrossSegmentExposureFake,
    DispatchWithoutIntentFake,
    DuplicateSideEffectFake,
    DraftSpeakingVoiceFake,
    DuplicateTurnExecutionFake,
    ForgedReadReceiptSurface,
    GoodCapabilityProviderFake,
    GoodCorePublicFake,
    GoodMemoryFake,
    GoodOperationProviderFake,
    GoodRuntimeLifecycleFake,
    GoodSurfaceFake,
    GoodVoiceFake,
    RegressingExposureFake,
    RewriteCommittedSegmentFake,
    ScopeIgnoringProviderFake,
    StaleGenerationProviderFake,
)
