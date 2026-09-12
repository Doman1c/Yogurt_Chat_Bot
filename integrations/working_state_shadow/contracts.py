"""W2 analysis contracts. No production authority and no raw report text."""

from enum import Enum
from typing import Literal, Optional, Tuple

from pydantic import Field, model_validator

from yogurt_contracts.foundation.model import FrozenContract


SCHEMA_VERSION: Literal["working-state-shadow-v1"] = "working-state-shadow-v1"


class AssistantOutcome(str, Enum):
    FINALIZED = "finalized"
    ABORTED = "aborted"
    NONE = "none"


class ShadowReplayTurn(FrozenContract):
    user_text: str = Field(max_length=16000)
    assistant_text: Optional[str] = Field(default=None, max_length=16000)
    assistant_outcome: AssistantOutcome = AssistantOutcome.NONE
    observation_offset_seconds: int = Field(default=0, ge=0, le=31536000)
    sample_delay_seconds: int = Field(default=0, ge=0, le=31536000)

    @model_validator(mode="after")
    def no_phantom_assistant(self) -> "ShadowReplayTurn":
        if self.assistant_outcome == AssistantOutcome.NONE and self.assistant_text is not None:
            raise ValueError("assistant text requires an assistant outcome")
        return self


class ShadowConversationCase(FrozenContract):
    case_id: str = Field(min_length=1, max_length=128)
    turns: Tuple[ShadowReplayTurn, ...] = Field(default=(), max_length=1000)

    @model_validator(mode="after")
    def ordered_time(self) -> "ShadowConversationCase":
        previous_sample = 0
        for turn in self.turns:
            if turn.observation_offset_seconds < previous_sample:
                raise ValueError("fixture time must not move backwards after a sample")
            previous_sample = turn.observation_offset_seconds + turn.sample_delay_seconds
        return self


class LegacyReplayConfig(FrozenContract):
    topic_ttl_minutes: int = Field(default=60, ge=1, le=10080)
    mood_ttl_minutes: int = Field(default=30, ge=1, le=1440)
    thread_ttl_minutes: int = Field(default=1440, ge=1, le=43200)


class ShadowFixtureSet(FrozenContract):
    schema_version: Literal["working-state-shadow-v1"] = SCHEMA_VERSION
    core_derivation_version: Literal["working-state-v1"] = "working-state-v1"
    legacy_config: LegacyReplayConfig = Field(default_factory=LegacyReplayConfig)
    cases: Tuple[ShadowConversationCase, ...] = Field(max_length=100)

    @model_validator(mode="after")
    def unique_cases(self) -> "ShadowFixtureSet":
        if len({case.case_id for case in self.cases}) != len(self.cases):
            raise ValueError("duplicate case identity")
        return self


class ShadowPresenceClass(str, Enum):
    BOTH_PRESENT = "both_present"
    LEGACY_ONLY = "legacy_only"
    CORE_ONLY = "core_only"
    BOTH_EMPTY = "both_empty"


class ShadowFieldOwnership(str, Enum):
    STRUCTURAL_COMPARISON = "structural_comparison"
    CORE_ONLY_SIGNAL = "core_only_signal"
    LEGACY_ONLY_UNMIGRATED = "legacy_only_unmigrated"


class ShadowPolicyDifference(str, Enum):
    LEGACY_TOPIC_TTL = "legacy_topic_ttl_core_focus_present"
    LEGACY_THREAD_TTL = "legacy_thread_ttl_core_threads_active"
    CORE_TURN_DISTANCE = "core_turn_distance_legacy_threads_active"


class LegacyOnlyFieldPresence(FrozenContract):
    dialogue_act_present: bool = False
    non_neutral_mood_present: bool = False
    non_default_interaction_mode_present: bool = False
    recent_actions_present: bool = False
    last_response_pattern_present: bool = False


class LegacyConversationStateView(FrozenContract):
    """Lossy, immutable metadata translated from the actual legacy representation."""

    state_version: int = Field(ge=0)
    focus_present: bool
    thread_count: int = Field(ge=0, le=20)
    topic_expired: bool = False
    threads_expired: bool = False
    legacy_only_fields: LegacyOnlyFieldPresence = Field(default_factory=LegacyOnlyFieldPresence)


class WorkingStateShadowSample(FrozenContract):
    schema_version: Literal["working-state-shadow-v1"] = SCHEMA_VERSION
    core_derivation_version: Literal["working-state-v1"] = "working-state-v1"
    unresolved_ownership: Literal["core_only_signal"] = "core_only_signal"
    legacy_only_ownership: Literal["legacy_only_unmigrated"] = "legacy_only_unmigrated"
    sample_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    core_revision: int = Field(ge=0)
    legacy_state_version: int = Field(ge=0)
    focus_presence: ShadowPresenceClass
    thread_presence: ShadowPresenceClass
    legacy_thread_count: int = Field(ge=0, le=20)
    core_active_thread_count: int = Field(ge=0, le=5)
    core_expired_thread_count: int = Field(ge=0, le=5)
    core_unresolved_count: int = Field(ge=0, le=5)
    core_resolved_count: int = Field(ge=0, le=5)
    legacy_topic_expired: bool
    legacy_threads_expired: bool
    legacy_only_field_presence: LegacyOnlyFieldPresence
    policy_differences: Tuple[ShadowPolicyDifference, ...] = Field(default=(), max_length=3)

    @model_validator(mode="after")
    def consistent_counts(self) -> "WorkingStateShadowSample":
        if self.core_active_thread_count + self.core_expired_thread_count > 5 or self.core_unresolved_count + self.core_resolved_count > 5:
            raise ValueError("Core W1 collection bounds exceeded")
        expected = ((ShadowPresenceClass.BOTH_EMPTY, ShadowPresenceClass.CORE_ONLY),
                    (ShadowPresenceClass.LEGACY_ONLY, ShadowPresenceClass.BOTH_PRESENT))
        if self.thread_presence != expected[int(self.legacy_thread_count > 0)][int(self.core_active_thread_count > 0)]:
            raise ValueError("thread class disagrees with counts")
        if len(set(self.policy_differences)) != len(self.policy_differences):
            raise ValueError("duplicate policy difference")
        return self


class PresenceCounts(FrozenContract):
    both_present: int = Field(ge=0)
    both_empty: int = Field(ge=0)
    legacy_only: int = Field(ge=0)
    core_only: int = Field(ge=0)


class CountBucket(FrozenContract):
    value: int
    count: int = Field(ge=1)


class LegacyOnlyCounts(FrozenContract):
    dialogue_act_present: int = Field(ge=0)
    non_neutral_mood_present: int = Field(ge=0)
    non_default_interaction_mode_present: int = Field(ge=0)
    recent_actions_present: int = Field(ge=0)
    last_response_pattern_present: int = Field(ge=0)


class PolicyDifferenceCounts(FrozenContract):
    legacy_topic_ttl: int = Field(ge=0)
    legacy_thread_ttl: int = Field(ge=0)
    core_turn_distance: int = Field(ge=0)


class WorkingStateShadowSummary(FrozenContract):
    schema_version: Literal["working-state-shadow-v1"] = SCHEMA_VERSION
    core_derivation_version: Literal["working-state-v1"] = "working-state-v1"
    focus_and_threads_ownership: Literal["structural_comparison"] = "structural_comparison"
    unresolved_ownership: Literal["core_only_signal"] = "core_only_signal"
    legacy_only_ownership: Literal["legacy_only_unmigrated"] = "legacy_only_unmigrated"
    legacy_config: LegacyReplayConfig
    sample_count: int = Field(ge=0)
    focus: PresenceCounts
    threads: PresenceCounts
    thread_count_delta: Tuple[CountBucket, ...] = Field(max_length=26)
    core_active_threads: Tuple[CountBucket, ...] = Field(max_length=6)
    core_expired_threads: Tuple[CountBucket, ...] = Field(max_length=6)
    core_unresolved_items: Tuple[CountBucket, ...] = Field(max_length=6)
    legacy_only_fields: LegacyOnlyCounts
    policy_differences: PolicyDifferenceCounts

    @model_validator(mode="after")
    def consistent_totals(self) -> "WorkingStateShadowSummary":
        for counts in (self.focus, self.threads):
            if counts.both_present + counts.both_empty + counts.legacy_only + counts.core_only != self.sample_count:
                raise ValueError("presence counts must partition samples")
        for histogram, low, high in ((self.thread_count_delta, -20, 5), (self.core_active_threads, 0, 5),
                                     (self.core_expired_threads, 0, 5), (self.core_unresolved_items, 0, 5)):
            values = tuple(bucket.value for bucket in histogram)
            if values != tuple(sorted(set(values))) or any(not low <= value <= high for value in values):
                raise ValueError("invalid ordered histogram")
            if sum(bucket.count for bucket in histogram) != self.sample_count:
                raise ValueError("histogram must partition samples")
        return self
