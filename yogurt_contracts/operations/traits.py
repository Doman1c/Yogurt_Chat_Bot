from enum import Enum

from ..foundation.model import FrozenContract


class EffectKind(str, Enum): NONE = "none"; READ = "read"; WRITE = "write"; PHYSICAL = "physical"
class IdempotencyKind(str, Enum): NATURAL = "natural"; KEYED = "keyed"; NONE = "none"
class ReversibilityKind(str, Enum): REVERSIBLE = "reversible"; COMPENSATABLE = "compensatable"; IRREVERSIBLE = "irreversible"
class CancellationSupport(str, Enum): BEFORE_DISPATCH_ONLY = "before_dispatch_only"; BEST_EFFORT = "best_effort"; PROVIDER_SUPPORTED = "provider_supported"; NOT_SUPPORTED = "not_supported"


class OperationTraits(FrozenContract):
    effect_kind: EffectKind
    idempotency: IdempotencyKind
    reversibility: ReversibilityKind
    cancellation_support: CancellationSupport

    def is_externally_side_effecting(self) -> bool:
        return self.effect_kind in {EffectKind.WRITE, EffectKind.PHYSICAL}
