"""Offline fixture-to-ledger replay. Never creates a YogurtCore/model runtime."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import List, Optional, Tuple

from yogurt_contracts.conversation.fingerprint import turn_fingerprint
from yogurt_contracts.conversation.input import TextInputPart, TurnInput
from yogurt_contracts.conversation.lifecycle import AbortReason, TurnState
from yogurt_contracts.conversation.refs import ConversationRef, TurnOrigin, TurnRef
from yogurt_contracts.execution.turn_snapshot import TurnSnapshot
from yogurt_contracts.foundation.identifiers import (
    ConversationId, CoreInstanceId, ExternalPrincipalRef, IdentityResolutionRef,
    InputId, PersonaId, ResponseId, SegmentId, TurnId, UserId,
)
from yogurt_contracts.response.lifecycle import ResponseAbortReason
from yogurt_contracts.response.models import AgentOutputSegmentCommitted
from yogurt_core.context.working_state import WorkingStateProjectionService
from yogurt_core_adapters.state_store_sqlite import SQLiteCoreStateStore

from .comparison import compare, summarize
from .contracts import (
    AssistantOutcome, LegacyReplayConfig, ShadowConversationCase, ShadowFixtureSet,
    WorkingStateShadowSample, WorkingStateShadowSummary,
)
from .legacy import LegacyReplayAdapter


REPLAY_EPOCH = datetime(2026, 1, 1, tzinfo=timezone.utc)


async def replay_case(case: ShadowConversationCase, config: LegacyReplayConfig,
                      derivation_version: str = "working-state-v1") -> Tuple[WorkingStateShadowSample, ...]:
    # Neither DB paths nor runtime endpoints are caller-configurable. Each case
    # owns new files, so no case can observe another case or a production ledger.
    with TemporaryDirectory(prefix="yogurt-w2-replay-") as directory:
        root = Path(directory)
        legacy = LegacyReplayAdapter(root / "legacy.sqlite3", config)
        core = SQLiteCoreStateStore(root / "core.sqlite3")
        token = await core.acquire_authority(CoreInstanceId(value="w2-replay"))
        try:
            ref = ConversationRef(persona_id=PersonaId(value="replay-persona"),
                                  user_id=UserId(value="replay-user"),
                                  conversation_id=ConversationId(value="replay-conversation"))
            await core.create_conversation(token, ref)
            projector = WorkingStateProjectionService(core, derivation_version)
            origin = TurnOrigin(origin_surface="isolated-w2-replay",
                                external_principal_ref=ExternalPrincipalRef(value="replay-external"),
                                identity_resolution_ref=IdentityResolutionRef(value="replay-identity"))
            snapshot = TurnSnapshot(identity_revision="replay-v1", core_config_revision="replay-v1",
                                    turn_origin=origin, conversation_context_revision="replay-v1",
                                    principal_binding_reference="replay-binding")
            samples: List[WorkingStateShadowSample] = []
            through: Optional[TurnRef] = None
            if not case.turns:
                samples.append(compare(case.case_id, 0, legacy.view(REPLAY_EPOCH), await projector.project(ref, None)))
            for ordinal, turn in enumerate(case.turns, 1):
                now = REPLAY_EPOCH + timedelta(seconds=turn.observation_offset_seconds)
                legacy.observe_user(turn.user_text, now)
                turn_id = TurnId(value="replay-turn-" + str(ordinal))
                turn_input = TurnInput(input_id=InputId(value="replay-input-" + str(ordinal)),
                                       parts=(TextInputPart(text=turn.user_text),), origin=origin)
                await core.try_admit_turn(token, ref, turn_id, turn_fingerprint(turn_input, "replay-binding"), snapshot, turn_input)
                through = (await core.get_turn(ref, turn_id)).turn_ref
                if turn.assistant_outcome != AssistantOutcome.NONE:
                    response_id = ResponseId(value="replay-response-" + str(ordinal))
                    response = await core.commit_response_started(token, ref, turn_id, response_id)
                    if turn.assistant_text:
                        segment = AgentOutputSegmentCommitted(response_id=response_id,
                                                              segment_id=SegmentId(value="replay-segment-" + str(ordinal)),
                                                              sequence=1, text=turn.assistant_text, language="und")
                        response = await core.commit_segment(token, response_id, response.revision, segment)
                    if turn.assistant_outcome == AssistantOutcome.FINALIZED:
                        await core.finalize_response(token, response_id, response.revision)
                        legacy.observe_assistant(turn.assistant_text or "", now)
                    else:
                        await core.abort_response(token, response_id, response.revision, ResponseAbortReason.EXECUTION_FAILURE)
                samples.append(compare(case.case_id, ordinal,
                                       legacy.view(now + timedelta(seconds=turn.sample_delay_seconds)),
                                       await projector.project(ref, through)))
                stored = await core.get_turn(ref, turn_id)
                # Finish fixture scheduling using existing legal Core transitions.
                # NONE cancels only the turn claim; there is no invented response.
                if turn.assistant_outcome == AssistantOutcome.FINALIZED:
                    await core.terminalize_turn(token, ref, turn_id, stored.revision, TurnState.COMPLETED)
                elif turn.assistant_outcome == AssistantOutcome.ABORTED:
                    await core.terminalize_turn(token, ref, turn_id, stored.revision, TurnState.ABORTED, AbortReason.EXECUTION_FAILURE)
                else:
                    await core.terminalize_turn(token, ref, turn_id, stored.revision, TurnState.CANCELLED)
            return tuple(samples)
        finally:
            await core.release_authority(token)
            await core.close()


async def replay(fixtures: ShadowFixtureSet) -> Tuple[Tuple[WorkingStateShadowSample, ...], WorkingStateShadowSummary]:
    samples: List[WorkingStateShadowSample] = []
    for case in fixtures.cases:
        samples.extend(await replay_case(case, fixtures.legacy_config, fixtures.core_derivation_version))
    result = tuple(samples)
    return result, summarize(result, fixtures.legacy_config)
