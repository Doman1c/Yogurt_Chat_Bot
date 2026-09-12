"""W2 isolated replay acceptance; synthetic inputs only."""

import asyncio
import hashlib
import inspect
import json
from pathlib import Path
import socket
import subprocess
import sys

import pytest
from pydantic import ValidationError

from integrations.working_state_shadow.comparison import FIELD_OWNERSHIP, compare, presence, report_json, summarize
from integrations.working_state_shadow.contracts import (
    AssistantOutcome, LegacyConversationStateView, LegacyReplayConfig,
    ShadowConversationCase, ShadowFieldOwnership, ShadowFixtureSet, ShadowPolicyDifference,
    ShadowPresenceClass, ShadowReplayTurn, WorkingStateShadowSample, WorkingStateShadowSummary,
)
from integrations.working_state_shadow.legacy import LegacyReplayAdapter, _factory
from integrations.working_state_shadow.replay import REPLAY_EPOCH, replay, replay_case
from yogurt_contracts.conversation.refs import ConversationRef, TurnRef
from yogurt_contracts.conversation.working_state import ConversationWorkingState, WorkingStateLifecycle
from yogurt_contracts.foundation.identifiers import ConversationId, PersonaId, TurnId, UserId
from yogurt_core.context.working_state import WorkingStateProjectionService


ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests/fixtures/working_state_shadow.json"


@pytest.fixture(scope="module")
def fixtures():
    return ShadowFixtureSet.model_validate_json(FIXTURE.read_bytes())


@pytest.fixture(scope="module")
def replayed(fixtures):
    return asyncio.run(replay(fixtures))


@pytest.mark.parametrize("legacy,core,expected", [
    (False, False, ShadowPresenceClass.BOTH_EMPTY),
    (True, False, ShadowPresenceClass.LEGACY_ONLY),
    (False, True, ShadowPresenceClass.CORE_ONLY),
    (True, True, ShadowPresenceClass.BOTH_PRESENT),
])
def test_structural_presence_classes(legacy, core, expected):
    assert presence(legacy, core) == expected


def test_comparison_contract_and_ownership_matrix(replayed):
    sample = replayed[0][0]
    assert WorkingStateShadowSample.model_validate_json(sample.model_dump_json()) == sample
    with pytest.raises(ValidationError):
        sample.core_revision = 100
    fields = sample.model_dump()
    for invalid in ({"user_id": "private"}, {"core_active_thread_count": 6},
                    {"sample_id": "plaintext"}, {"core_revision": "1"},
                    {"thread_presence": ShadowPresenceClass.LEGACY_ONLY}):
        with pytest.raises(ValidationError):
            WorkingStateShadowSample(**dict(fields, **invalid))
    assert FIELD_OWNERSHIP[:2] == (
        ("current_topic", "recent_focus", ShadowFieldOwnership.STRUCTURAL_COMPARISON),
        ("open_threads", "active_threads", ShadowFieldOwnership.STRUCTURAL_COMPARISON))
    assert FIELD_OWNERSHIP[2] == (None, "unresolved_items", ShadowFieldOwnership.CORE_ONLY_SIGNAL)
    assert {row[0] for row in FIELD_OWNERSHIP[3:]} == {"dialogue_act", "user_mood", "interaction_mode", "recent_actions", "last_response_pattern"}
    assert all(row[2] == ShadowFieldOwnership.LEGACY_ONLY_UNMIGRATED for row in FIELD_OWNERSHIP[3:])


def test_fixture_validation_is_bounded_and_deterministic():
    for text in ('{"cases":[],"schema_version":"v99"}', '{"cases":[],"core_derivation_version":"v2"}',
                 '{"cases":[{"case_id":"same"},{"case_id":"same"}]}'):
        with pytest.raises(ValidationError):
            ShadowFixtureSet.model_validate_json(text)
    with pytest.raises(ValidationError):
        ShadowReplayTurn(user_text="x" * 16001)
    with pytest.raises(ValidationError):
        ShadowReplayTurn(user_text="x", assistant_text="phantom")
    with pytest.raises(ValidationError):
        ShadowConversationCase(case_id="time", turns=(ShadowReplayTurn(user_text="x", sample_delay_seconds=5), ShadowReplayTurn(user_text="y", observation_offset_seconds=4)))


def test_question_lifecycle_finalized_aborted_and_none(fixtures):
    cases = {case.case_id: case for case in fixtures.cases}
    for name, active, resolved, legacy_version in (("question-finalized", 0, 1, 2), ("question-pending", 1, 0, 1), ("question-aborted", 1, 0, 1)):
        result = asyncio.run(replay_case(cases[name], fixtures.legacy_config))[0]
        assert (result.core_unresolved_count, result.core_resolved_count) == (active, resolved)
        assert result.legacy_state_version == legacy_version


def test_known_ttl_and_turn_distance_policy_differences(fixtures):
    cases = {case.case_id: case for case in fixtures.cases}
    ttl = asyncio.run(replay_case(cases["legacy-ttl"], fixtures.legacy_config))[0]
    assert ttl.legacy_topic_expired and ttl.legacy_threads_expired
    assert ttl.focus_presence == ttl.thread_presence == ShadowPresenceClass.CORE_ONLY
    assert ttl.core_active_thread_count == 1
    assert ttl.policy_differences == (ShadowPolicyDifference.LEGACY_TOPIC_TTL, ShadowPolicyDifference.LEGACY_THREAD_TTL)
    distance = asyncio.run(replay_case(cases["core-distance"], fixtures.legacy_config))[-1]
    assert distance.thread_presence == ShadowPresenceClass.LEGACY_ONLY
    assert distance.core_expired_thread_count == 1 and distance.legacy_thread_count == 1
    assert distance.policy_differences == (ShadowPolicyDifference.CORE_TURN_DISTANCE,)
    assert not distance.legacy_threads_expired


def test_legacy_only_fields_are_metadata_not_core_failures(fixtures):
    cases = {case.case_id: case for case in fixtures.cases}
    mood = asyncio.run(replay_case(cases["mood"], fixtures.legacy_config))[0]
    mode = asyncio.run(replay_case(cases["mode"], fixtures.legacy_config))[0]
    assert mood.legacy_only_field_presence.non_neutral_mood_present
    assert mode.legacy_only_field_presence.non_default_interaction_mode_present
    assert mood.core_unresolved_count == mode.core_unresolved_count == 0
    assert "mismatch" not in mood.model_dump_json() and "failure" not in mode.model_dump_json()


def test_actual_w1_projection_threads_sources_bounds_and_unicode(fixtures, monkeypatch):
    original = WorkingStateProjectionService.project
    observed = []

    async def capture(self, conversation_ref, through_turn_ref):
        state = await original(self, conversation_ref, through_turn_ref)
        observed.append(state)
        return state

    monkeypatch.setattr(WorkingStateProjectionService, "project", capture)
    cases = {case.case_id: case for case in fixtures.cases}
    asyncio.run(replay_case(cases["repeat"], fixtures.legacy_config))
    assert len(observed) == 4
    assert len({state.active_threads[0].entry_id for state in observed}) == 1
    assert [ref.turn_id.value for ref in observed[-1].active_threads[0].source_turn_refs] == ["replay-turn-2", "replay-turn-3", "replay-turn-4"]
    assert observed[-1].active_threads[0].lifecycle == WorkingStateLifecycle.ACTIVE
    asyncio.run(replay_case(cases["long"], fixtures.legacy_config))
    assert len(observed[-1].recent_focus[0].value) == 160
    asyncio.run(replay_case(cases["unicode"], fixtures.legacy_config))
    assert observed[-1].recent_focus[0].value == "中文？ café 🍦"


def test_actual_legacy_adapter_uses_unchanged_source_and_explicit_clock(tmp_path):
    factory = _factory()
    assert Path(inspect.getfile(factory)).resolve() == ROOT / "social_layer/src/yogurt_social/conversation_state.py"
    adapter = LegacyReplayAdapter(tmp_path / "adapter.sqlite3", LegacyReplayConfig())
    actual = factory(tmp_path / "reference.sqlite3")
    text = "开始编写 V4 代码，然后本地同步，再开始测试"
    adapter.observe_user(text, REPLAY_EPOCH)
    state = actual.observe_user_turn("replay-user", "replay-conversation", text, now=REPLAY_EPOCH)
    view = adapter.view(REPLAY_EPOCH)
    assert state.current_topic == "Yogurt V4 开发"
    assert view.thread_count == len(state.open_threads) == 3
    assert view.state_version == state.state_version
    assert view.focus_present and view.legacy_only_fields.non_default_interaction_mode_present


def test_aggregate_partitions_exact_buckets_and_safe_output(replayed):
    samples, summary = replayed
    assert summary.sample_count == len(samples) == 25
    assert summary.focus.model_dump() == {"both_present": 22, "both_empty": 1, "legacy_only": 1, "core_only": 1}
    assert summary.threads.model_dump() == {"both_present": 9, "both_empty": 2, "legacy_only": 1, "core_only": 13}
    assert sum(bucket.count for bucket in summary.core_unresolved_items) == 25
    assert summary.policy_differences.model_dump() == {"legacy_topic_ttl": 1, "legacy_thread_ttl": 1, "core_turn_distance": 1}
    assert WorkingStateShadowSummary.model_validate_json(report_json(summary)) == summary
    for invalid in ({"sample_count": 26}, {"raw_text": "secret"}):
        with pytest.raises(ValidationError):
            WorkingStateShadowSummary(**dict(summary.model_dump(), **invalid))
    with pytest.raises(ValueError, match="duplicate shadow"):
        summarize(samples + samples[:1], LegacyReplayConfig())
    payload = report_json(summary)
    for forbidden in ("user_id", "persona_id", "conversation_key", "conversation_id", "user_text", "assistant_text", "auth", "prompt", "gate_evaluated_events", "准确率", "V4 开发"):
        assert forbidden not in payload


def test_safe_ids_case_isolation_and_repeatability(fixtures, replayed):
    samples, summary = asyncio.run(replay(fixtures))
    assert samples == replayed[0] and report_json(summary) == report_json(replayed[1])
    assert len({s.sample_id for s in samples}) == len(samples)
    for sample in samples:
        assert len(sample.sample_id) == 64
    left = ShadowConversationCase(case_id="private-case-sentinel", turns=(ShadowReplayTurn(user_text="SENSITIVE user text?", assistant_outcome=AssistantOutcome.NONE),))
    right = ShadowConversationCase(case_id="separate", turns=())
    combined, _ = asyncio.run(replay(ShadowFixtureSet(cases=(left, right))))
    assert combined[-1].core_revision == combined[-1].legacy_state_version == 0
    assert combined[-1].focus_presence == ShadowPresenceClass.BOTH_EMPTY
    assert combined[-1] == asyncio.run(replay_case(right, LegacyReplayConfig()))[0]
    assert "SENSITIVE" not in combined[0].model_dump_json()
    assert "private-case-sentinel" not in combined[0].sample_id


def test_replay_never_contacts_network(fixtures, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("network forbidden in replay")
    async def run():
        # Windows creates a loop-internal socketpair before running the coroutine.
        # Forbid all connections during the actual replay, not loop construction.
        with monkeypatch.context() as patch:
            patch.setattr(socket.socket, "connect", forbidden)
            assert (await replay(fixtures))[1].sample_count == 25
    asyncio.run(run())


def test_restart_cli_report_is_byte_equivalent_and_error_redacted(tmp_path, replayed):
    command = [sys.executable, "-m", "integrations.working_state_shadow", str(FIXTURE)]
    a = subprocess.run(command, capture_output=True, cwd=ROOT)
    b = subprocess.run(command, capture_output=True, cwd=ROOT)
    assert a.returncode == b.returncode == 0, (a.stderr, b.stderr)
    assert a.stdout == b.stdout
    # Windows stdout uses CRLF; canonical reporter content remains identical.
    assert a.stdout.replace(b"\r\n", b"\n") == report_json(replayed[1]).encode()
    invalid = tmp_path / "invalid.json"
    invalid.write_text('{"cases":[],"secret":"PRIVATE_AUTH_SENTINEL"}', encoding="utf-8")
    failed = subprocess.run(command[:-1] + [str(invalid)], capture_output=True, cwd=ROOT)
    assert failed.returncode == 1 and not failed.stdout
    assert b"PRIVATE_AUTH_SENTINEL" not in failed.stderr and b"Traceback" not in failed.stderr


def test_memory_provider_absent_still_replays():
    code = '''
import sys, asyncio
from importlib.abc import MetaPathFinder
from pathlib import Path
class NoMemory(MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith(("integrations.memory_v5", "yogurt_social", "social_layer", "yogurt_native")):
            raise ImportError("production/provider absent: " + fullname)
sys.meta_path.insert(0, NoMemory())
from integrations.working_state_shadow.contracts import ShadowFixtureSet
from integrations.working_state_shadow.replay import replay
fixture = ShadowFixtureSet.model_validate_json(Path("tests/fixtures/working_state_shadow.json").read_bytes())
assert asyncio.run(replay(fixture))[1].sample_count == 25
'''
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
