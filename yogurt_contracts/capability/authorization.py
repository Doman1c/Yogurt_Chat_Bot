from enum import Enum

from ..foundation.model import FrozenContract


class CapabilityAuthorizationDecisionKind(str, Enum): ALLOW = "allow"; DENY = "deny"


class CapabilityAuthorizationDecision(FrozenContract):
    decision: CapabilityAuthorizationDecisionKind
    reason: str
