"""Structural presence only; no accuracy, similarity or promotion decision."""

import hashlib
import json
from collections import Counter
from typing import Iterable, Tuple

from yogurt_contracts.conversation.working_state import ConversationWorkingState, WorkingStateLifecycle

from .contracts import (
    SCHEMA_VERSION, CountBucket, LegacyConversationStateView, LegacyOnlyCounts,
    LegacyReplayConfig, PolicyDifferenceCounts, PresenceCounts, ShadowFieldOwnership,
    ShadowPolicyDifference, ShadowPresenceClass, WorkingStateShadowSample, WorkingStateShadowSummary,
)


# Explicit ownership matrix: names identify concepts, never equivalent schemas.
FIELD_OWNERSHIP = (
    ("current_topic", "recent_focus", ShadowFieldOwnership.STRUCTURAL_COMPARISON),
    ("open_threads", "active_threads", ShadowFieldOwnership.STRUCTURAL_COMPARISON),
    (None, "unresolved_items", ShadowFieldOwnership.CORE_ONLY_SIGNAL),
    *((field, None, ShadowFieldOwnership.LEGACY_ONLY_UNMIGRATED) for field in (
        "dialogue_act", "user_mood", "interaction_mode", "recent_actions", "last_response_pattern")),
)


def presence(legacy: bool, core: bool) -> ShadowPresenceClass:
    if legacy:
        return ShadowPresenceClass.BOTH_PRESENT if core else ShadowPresenceClass.LEGACY_ONLY
    return ShadowPresenceClass.CORE_ONLY if core else ShadowPresenceClass.BOTH_EMPTY


def compare(case_id: str, ordinal: int, legacy: LegacyConversationStateView,
            core: ConversationWorkingState) -> WorkingStateShadowSample:
    if ordinal < 0 or not case_id or core.derivation_version != "working-state-v1":
        raise ValueError("invalid comparison boundary/version")
    identity = json.dumps((SCHEMA_VERSION, case_id, ordinal), ensure_ascii=True, separators=(",", ":"))
    active = sum(entry.lifecycle == WorkingStateLifecycle.ACTIVE for entry in core.active_threads)
    expired = sum(entry.lifecycle == WorkingStateLifecycle.EXPIRED for entry in core.active_threads)
    differences = []
    if legacy.topic_expired and core.recent_focus:
        differences.append(ShadowPolicyDifference.LEGACY_TOPIC_TTL)
    if legacy.threads_expired and active:
        differences.append(ShadowPolicyDifference.LEGACY_THREAD_TTL)
    if expired and legacy.thread_count:
        differences.append(ShadowPolicyDifference.CORE_TURN_DISTANCE)
    return WorkingStateShadowSample(
        sample_id=hashlib.sha256(identity.encode("utf-8")).hexdigest(),
        core_revision=core.revision, legacy_state_version=legacy.state_version,
        focus_presence=presence(legacy.focus_present, bool(core.recent_focus)),
        thread_presence=presence(legacy.thread_count > 0, active > 0),
        legacy_thread_count=legacy.thread_count, core_active_thread_count=active,
        core_expired_thread_count=expired,
        core_unresolved_count=sum(e.lifecycle == WorkingStateLifecycle.ACTIVE for e in core.unresolved_items),
        core_resolved_count=sum(e.lifecycle == WorkingStateLifecycle.RESOLVED for e in core.unresolved_items),
        legacy_topic_expired=legacy.topic_expired, legacy_threads_expired=legacy.threads_expired,
        legacy_only_field_presence=legacy.legacy_only_fields, policy_differences=tuple(differences),
    )


def _presence_counts(values: Iterable[ShadowPresenceClass]) -> PresenceCounts:
    counts = Counter(values)
    return PresenceCounts(both_present=counts[ShadowPresenceClass.BOTH_PRESENT],
                          both_empty=counts[ShadowPresenceClass.BOTH_EMPTY],
                          legacy_only=counts[ShadowPresenceClass.LEGACY_ONLY],
                          core_only=counts[ShadowPresenceClass.CORE_ONLY])


def _histogram(values: Iterable[int]) -> Tuple[CountBucket, ...]:
    return tuple(CountBucket(value=value, count=count) for value, count in sorted(Counter(values).items()))


def summarize(samples: Tuple[WorkingStateShadowSample, ...], config: LegacyReplayConfig) -> WorkingStateShadowSummary:
    if len({s.sample_id for s in samples}) != len(samples):
        raise ValueError("duplicate shadow sample")
    return WorkingStateShadowSummary(
        legacy_config=config, sample_count=len(samples),
        focus=_presence_counts(s.focus_presence for s in samples),
        threads=_presence_counts(s.thread_presence for s in samples),
        thread_count_delta=_histogram(s.core_active_thread_count - s.legacy_thread_count for s in samples),
        core_active_threads=_histogram(s.core_active_thread_count for s in samples),
        core_expired_threads=_histogram(s.core_expired_thread_count for s in samples),
        core_unresolved_items=_histogram(s.core_unresolved_count for s in samples),
        legacy_only_fields=LegacyOnlyCounts(
            dialogue_act_present=sum(s.legacy_only_field_presence.dialogue_act_present for s in samples),
            non_neutral_mood_present=sum(s.legacy_only_field_presence.non_neutral_mood_present for s in samples),
            non_default_interaction_mode_present=sum(s.legacy_only_field_presence.non_default_interaction_mode_present for s in samples),
            recent_actions_present=sum(s.legacy_only_field_presence.recent_actions_present for s in samples),
            last_response_pattern_present=sum(s.legacy_only_field_presence.last_response_pattern_present for s in samples)),
        policy_differences=PolicyDifferenceCounts(
            legacy_topic_ttl=sum(ShadowPolicyDifference.LEGACY_TOPIC_TTL in s.policy_differences for s in samples),
            legacy_thread_ttl=sum(ShadowPolicyDifference.LEGACY_THREAD_TTL in s.policy_differences for s in samples),
            core_turn_distance=sum(ShadowPolicyDifference.CORE_TURN_DISTANCE in s.policy_differences for s in samples)),
    )


def report_json(summary: WorkingStateShadowSummary) -> str:
    return json.dumps(summary.model_dump(mode="json"), ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n"
