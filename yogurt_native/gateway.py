"""Authenticated ingress and read projections; never a second chat ledger."""
import asyncio
import hashlib
import hmac
import json
import time
from typing import AsyncIterator, Optional, Tuple
from yogurt_contracts.conversation.fingerprint import turn_fingerprint
from yogurt_contracts.conversation.input import TextInputPart, TurnInput
from yogurt_contracts.conversation.refs import ConversationRef, TurnOrigin
from yogurt_contracts.foundation.identifiers import ConversationId, InputId, ResponseId, TurnId
from yogurt_contracts.identity.principal import ExternalPrincipal
from yogurt_contracts.response.events import ResponseAbortedEvent, ResponseFinalizedEvent, SegmentCommittedEvent
from yogurt_contracts.response.lifecycle import ResponseAbortReason
from yogurt_contracts.surface import (NativeSurfaceTurnRequest, SurfaceConversationRef,
    SurfaceConversationHistory, SurfaceHistoryTurn, SurfaceResponseEvent, SurfaceTurnAccepted)
from yogurt_core.application.commands import CallerContext, GetResponseCommand, GetTurnCommand, SubmitTurnCommand, WatchResponseCommand
from yogurt_core.application.core import YogurtCore
from yogurt_core.application.results import TurnAdmissionStatus
from yogurt_core.ports.state_store import CoreStateError
from .identity import OwnerProfile
from .telemetry import LatencyTelemetry

class SurfaceFailure(Exception):
    def __init__(self, code: str, status: int) -> None:
        self.code, self.status = code, status

class NativeGateway:
    def __init__(self, core: YogurtCore, mapping_key: bytes, telemetry: LatencyTelemetry,
                 token_streaming: bool = False) -> None:
        if len(mapping_key) < 32:
            raise ValueError("stable mapping key must contain at least 32 bytes")
        self.core, self._key, self.telemetry = core, mapping_key, telemetry
        self.token_streaming = token_streaming

    def _id(self, kind: str, *parts: str) -> str:
        value = json.dumps((kind,) + parts, ensure_ascii=False, separators=(",", ":"))
        return kind + "-" + hmac.new(self._key, value.encode(), hashlib.sha256).hexdigest()

    @staticmethod
    def ref(profile: OwnerProfile, conversation_id: str) -> ConversationRef:
        return ConversationRef(persona_id=profile.persona_id, user_id=profile.user_id,
                               conversation_id=ConversationId(value=conversation_id))

    async def submit(self, profile: OwnerProfile, request: NativeSurfaceTurnRequest, received_at: Optional[float] = None) -> SurfaceTurnAccepted:
        identity = (profile.persona_id.value, profile.user_id.value)
        cid = request.conversation_id or self._id("conversation", *identity, request.client_message_id)
        ref = self.ref(profile, cid)
        tid = TurnId(value=self._id("turn", *identity, cid, request.client_message_id))
        self.telemetry.received(ref, tid, time.monotonic() if received_at is None else received_at)
        if request.conversation_id is None:
            await self.core.create_conversation(ref)
        else:
            await self.core.conversation_turns(ref, CallerContext(logical_user_id=profile.user_id))
        # Bind the exact complete wire payload (including advisory metadata) into
        # the existing Core fingerprint. This also detects Unicode/line-ending changes.
        digest = hashlib.sha256(request.model_dump_json().encode()).hexdigest()
        binding = "native-1:" + profile.principal_ref.value + ":" + digest
        turn_input = TurnInput(input_id=InputId(value=self._id("input", tid.value)),
            parts=(TextInputPart(text=request.text),), origin=TurnOrigin(origin_surface="native",
            external_principal_ref=profile.principal_ref, identity_resolution_ref=profile.resolution_ref))
        caller = CallerContext(logical_user_id=profile.user_id)
        result = await self.core.submit_turn(SubmitTurnCommand(caller_context=caller,
            external_principal=ExternalPrincipal(reference=profile.principal_ref, authenticated_surface="native"),
            conversation_ref=ref, turn_id=tid, input=turn_input,
            fingerprint=turn_fingerprint(turn_input, binding), principal_binding_reference=binding,
            core_config_revision="native-1", conversation_context_revision="core-projection"))
        if result.status == TurnAdmissionStatus.TURN_ID_CONFLICT:
            raise SurfaceFailure("CLIENT_MESSAGE_CONFLICT", 409)
        if result.status == TurnAdmissionStatus.CONVERSATION_BUSY:
            raise SurfaceFailure("CONVERSATION_BUSY", 409)
        if result.status == TurnAdmissionStatus.REJECTED:
            raise SurfaceFailure("TURN_REJECTED", 403)
        async def response_ready() -> ResponseId:
            while True:
                turn = await self.core.get_turn(GetTurnCommand(caller_context=caller, conversation_ref=ref, turn_id=tid))
                if turn.response_id is not None:
                    return turn.response_id
                if turn.state.value in {"cancelled", "aborted", "completed"}:
                    raise SurfaceFailure("TURN_TERMINATED_BEFORE_RESPONSE", 409)
                await asyncio.sleep(0.005)
        try:
            rid = await asyncio.wait_for(response_ready(), timeout=2.0)
        except asyncio.TimeoutError:
            raise SurfaceFailure("CORE_UNAVAILABLE", 503) from None
        return SurfaceTurnAccepted(conversation_id=cid, turn_ref=tid.value, response_id=rid.value,
            event_stream_url="/v1/native/responses/" + rid.value + "/events",
            admission="SAME_LOGICAL_TURN" if result.status == TurnAdmissionStatus.SAME_LOGICAL_TURN else "NEWLY_ADMITTED")

    async def authorize_response(self, profile: OwnerProfile, response_id: str) -> None:
        ref = await self.core.response_conversation(GetResponseCommand(
            caller_context=CallerContext(logical_user_id=profile.user_id), response_id=ResponseId(value=response_id)))
        if ref.persona_id != profile.persona_id:
            raise CoreStateError("READ_NOT_AUTHORIZED")

    async def events(self, profile: OwnerProfile, response_id: str, after: int = 0) -> AsyncIterator[str]:
        await self.authorize_response(profile, response_id)
        command = WatchResponseCommand(caller_context=CallerContext(logical_user_id=profile.user_id),
                                       response_id=ResponseId(value=response_id), after_event_sequence=after)
        async for event in self.core.watch_response(command):
            if isinstance(event, SegmentCommittedEvent):
                projected = SurfaceResponseEvent(response_id=event.response_id.value, sequence=event.event_sequence, event="response.delta" if self.token_streaming else "response.text", text=event.text)
            elif isinstance(event, ResponseFinalizedEvent):
                projected = SurfaceResponseEvent(response_id=event.response_id.value, sequence=event.event_sequence, event="response.completed")
            elif isinstance(event, ResponseAbortedEvent):
                projected = SurfaceResponseEvent(response_id=event.response_id.value, sequence=event.event_sequence, event="response.cancelled" if event.reason == ResponseAbortReason.CANCELLED else "response.failed", reason=event.reason.value)
            else:
                projected = SurfaceResponseEvent(response_id=event.response_id.value, sequence=event.event_sequence, event="response.started",
                    reason=None if self.token_streaming else "MODEL_TOKEN_STREAMING_NOT_YET_AVAILABLE")
            yield "id: " + str(event.event_sequence) + "\nevent: " + projected.event + "\ndata: " + projected.model_dump_json() + "\n\n"

    async def conversations(self, profile: OwnerProfile) -> Tuple[SurfaceConversationRef, ...]:
        refs = await self.core.list_conversations(CallerContext(logical_user_id=profile.user_id))
        return tuple(SurfaceConversationRef(conversation_id=r.conversation_id.value) for r in refs if r.persona_id == profile.persona_id)

    async def history(self, profile: OwnerProfile, conversation_id: str) -> SurfaceConversationHistory:
        caller = CallerContext(logical_user_id=profile.user_id)
        turns = await self.core.conversation_turns(self.ref(profile, conversation_id), caller)
        rows = []
        for turn in turns:
            response_text = ""
            if turn.response_id is not None:
                response = await self.core.get_response(GetResponseCommand(caller_context=caller, response_id=turn.response_id))
                response_text = "".join(segment.text for segment in response.segments)
            rows.append(SurfaceHistoryTurn(turn_ref=turn.turn_ref.turn_id.value,
                text="".join(part.text for part in turn.turn_input.parts if isinstance(part, TextInputPart)),
                response_id=turn.response_id.value if turn.response_id else None,
                response_text=response_text, state=turn.state.value))
        return SurfaceConversationHistory(conversation_id=conversation_id, turns=tuple(rows))
