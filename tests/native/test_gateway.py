"""Native S1/S2 behavioral gates against the durable Core, without social packages."""
import asyncio
import hashlib
import subprocess
import sys
import time
import json
from pathlib import Path

import httpx
import pytest

from yogurt_contracts.foundation.identifiers import CoreInstanceId, ExternalPrincipalRef, IdentityResolutionRef, PersonaId, UserId, TurnId
from yogurt_contracts.execution.persona import PersonaSnapshot
from yogurt_contracts.surface import NativeSurfaceTurnRequest
from yogurt_core.application.commands import CallerContext, CancelTurnCommand
from yogurt_core.application.core import YogurtCore
from yogurt_core.model.events import VisibleTextDelta, AttemptTerminal, AttemptTerminalKind
from yogurt_core_adapters.persona_fake import FakePersonaSnapshotPort
from yogurt_core_adapters.state_store_sqlite import SQLiteCoreStateStore
from yogurt_native.gateway import NativeGateway
from yogurt_native.http import create_app, MAX_BODY_BYTES
from yogurt_native.identity import Credential, OwnerProfile, ServerIdentityMapping
from yogurt_native.telemetry import LatencyTelemetry

TOKEN = "test-token-" + "x" * 32
KEY = b"stable-id-mapping-key-0123456789012345"
HEADERS = {"Authorization": "Bearer " + TOKEN}

class Model:
    def __init__(self, mode="ok", block=False):
        self.mode = mode
        self.release = asyncio.Event()
        self.entered = asyncio.Event()
        self.contexts = []
        self.cancelled = 0
        if not block:
            self.release.set()

    async def run(self, context, attempt):
        self.contexts.append(context)
        self.entered.set()
        await self.release.wait()
        if self.mode == "raise":
            raise RuntimeError("private prompt must not escape")
        if self.mode == "ok":
            yield VisibleTextDelta(text="answer")
        else:
            yield AttemptTerminal(kind=AttemptTerminalKind(self.mode))

    async def cancel(self, attempt):
        self.cancelled += 1

async def setup(path, model=None):
    model = model or Model()
    profile = OwnerProfile(PersonaId(value="yogurt"), UserId(value="owner"),
        ExternalPrincipalRef(value="native-owner"), IdentityResolutionRef(value="owner-v1"))
    outsider = OwnerProfile(PersonaId(value="yogurt"), UserId(value="other"),
        ExternalPrincipalRef(value="other"), IdentityResolutionRef(value="other-v1"))
    other_persona = OwnerProfile(PersonaId(value="other-persona"), profile.user_id,
        ExternalPrincipalRef(value="other-persona"), IdentityResolutionRef(value="other-persona-v1"))
    identities = ServerIdentityMapping((Credential(TOKEN, profile), Credential("z" * 40, outsider), Credential("p" * 40, other_persona)))
    persona = PersonaSnapshot(persona_id=profile.persona_id, persona_revision="1", soul_revision="1",
        identity_revision="1", relationship_context_revision="1", model_context_material="test persona")
    store = SQLiteCoreStateStore(path)
    telemetry = LatencyTelemetry()
    core = YogurtCore(store, identities, FakePersonaSnapshotPort({"yogurt": persona}), model,
                      CoreInstanceId(value="native-test"), observer=telemetry)
    gateway = NativeGateway(core, KEY, telemetry)
    app = create_app(gateway, identities)
    await core.start()
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://native", headers=HEADERS)
    return core, store, gateway, profile, client, model, app

async def close(core, store, client):
    await client.aclose()
    await core.stop()
    await store.close()

def payload(message="m1", **overrides):
    return dict(client_message_id=message, text="private user text", surface_type="native_ios", **overrides)

async def post(client, body=None):
    response = await client.post("/v1/native/turns", json=body or payload())
    assert response.status_code == 202, response.text
    return response.json()

def test_accepted_before_model_output_and_disconnect_replay(tmp_path):
    async def scenario():
        core, store, gateway, profile, client, model, app = await setup(tmp_path / "core.db", Model(block=True))
        accepted = await asyncio.wait_for(post(client), 1)
        await model.entered.wait()
        assert not model.release.is_set()
        assert accepted["turn_ref"] != "m1"
        stream = gateway.events(profile, accepted["response_id"])
        started = await stream.__anext__()
        assert "response.started" in started
        assert "MODEL_TOKEN_STREAMING_NOT_YET_AVAILABLE" in started
        await stream.aclose()  # Consumer disconnect has no authority to cancel.
        assert model.cancelled == 0
        model.release.set()
        await core.drain()
        replay = await client.get(accepted["event_stream_url"])
        assert replay.status_code == 200
        assert replay.text.startswith(started)
        assert "response.text" in replay.text and "response.completed" in replay.text
        assert "response.delta" not in replay.text
        resumed = await client.get(accepted["event_stream_url"], headers={"Last-Event-ID": "1"})
        assert "response.started" not in resumed.text and "id: 2" in resumed.text
        assert (await client.get(accepted["event_stream_url"], headers={"Last-Event-ID": "3"})).text == ""
        await close(core, store, client)
    asyncio.run(scenario())

def test_duplicate_concurrent_conflict_and_restart(tmp_path):
    async def scenario():
        path = tmp_path / "core.db"
        core, store, gateway, profile, client, model, app = await setup(path)
        a, b = await asyncio.gather(post(client), post(client))
        assert (a["turn_ref"], a["response_id"]) == (b["turn_ref"], b["response_id"])
        assert {a["admission"], b["admission"]} == {"NEWLY_ADMITTED", "SAME_LOGICAL_TURN"}
        await core.drain()
        for change in ({"text": "different"}, {"device_instance_id": "changed"}, {"client_timestamp": "changed"}, {"surface_type": "native_macos"}):
            body = payload(); body.update(change)
            result = await client.post("/v1/native/turns", json=body)
            assert result.status_code == 409 and result.json()["code"] == "CLIENT_MESSAGE_CONFLICT"
        assert len(model.contexts) == 1
        await close(core, store, client)
        core, store, gateway, profile, client, model, app = await setup(path)
        retried = await post(client)
        assert retried["response_id"] == a["response_id"]
        assert retried["admission"] == "SAME_LOGICAL_TURN"
        assert not model.contexts
        assert len((await client.get("/v1/native/conversations")).json()) == 1
        assert "response.completed" in (await client.get(a["event_stream_url"])).text
        await close(core, store, client)
    asyncio.run(scenario())

def test_devices_share_authority_and_distinct_contexts(tmp_path):
    async def scenario():
        core, store, gateway, profile, client, model, app = await setup(tmp_path / "core.db")
        a = await post(client, payload(device_instance_id="iphone"))
        await core.drain()
        x = a["conversation_id"]
        history = await client.get("/v1/native/conversations/" + x)
        assert history.json()["turns"][0]["response_text"] == "answer"
        body = payload("m2", conversation_id=x, device_instance_id="mac")
        body["surface_type"] = "native_macos"
        b = await post(client, body)
        await core.drain()
        assert b["conversation_id"] == x
        assert [entry.text for entry in model.contexts[1].history] == ["private user text", "answer"]
        c = await post(client, payload("m3", device_instance_id="mac"))
        await core.drain()
        assert c["conversation_id"] != x and model.contexts[2].history == ()
        for token in ("z" * 40, "p" * 40):
            headers = {"Authorization": "Bearer " + token}
            assert (await client.get(a["event_stream_url"], headers=headers)).status_code == 404
            assert (await client.get("/v1/native/conversations/" + x, headers=headers)).status_code == 404
            assert (await client.post("/v1/native/turns", json=payload("m4", conversation_id=x), headers=headers)).status_code == 404
            assert (await client.get("/v1/native/conversations", headers=headers)).json() == []
        await close(core, store, client)
    asyncio.run(scenario())

@pytest.mark.parametrize("field,value", [("persona_id", "evil"), ("user_id", "evil"), ("turn_id", "evil"),
    ("response_id", "evil"), ("authority", {}), ("provider_binding", "evil"), ("memory_identity", "evil"),
    ("text", "x" * 16001), ("text", " "), ("text", 123), ("client_message_id", "x" * 129),
    ("client_message_id", "bad/newline\n"), ("device_instance_id", "x" * 129), ("surface_type", "wechat"),
    ("conversation_id", "../secret"), ("client_timestamp", "x" * 65)])
def test_bounded_input_identity_forgery(tmp_path, field, value):
    async def scenario():
        core, store, gateway, profile, client, model, app = await setup(tmp_path / "core.db")
        body = payload(); body[field] = value
        result = await client.post("/v1/native/turns", json=body)
        assert result.status_code == 422
        assert result.json() == {"code": "INVALID_REQUEST"}
        assert not model.contexts and await gateway.conversations(profile) == ()
        await close(core, store, client)
    asyncio.run(scenario())

def test_unauthorized_before_parsing_core_unavailable_and_invalid_resources(tmp_path):
    async def scenario():
        core, store, gateway, profile, client, model, app = await setup(tmp_path / "core.db")
        bad = await client.post("/v1/native/turns", content=b"not-json", headers={"Authorization": "Bearer wrong"})
        assert bad.status_code == 401 and TOKEN not in bad.text
        assert await gateway.conversations(profile) == ()
        huge = await client.post("/v1/native/turns", content=b"x" * (MAX_BODY_BYTES + 1))
        assert huge.status_code == 413
        assert (await client.post("/v1/native/turns", json=payload(conversation_id="missing"))).status_code == 404
        assert (await client.get("/v1/native/responses/missing/events")).status_code == 404
        assert (await client.get("/v1/native/responses/missing/events", headers={"Last-Event-ID": "-1"})).status_code == 422
        await core.stop()
        assert (await client.post("/v1/native/turns", json=payload())).status_code == 503
        assert (await client.get("/v1/native/health")).status_code == 503
        await client.aclose(); await store.close()
    asyncio.run(scenario())

@pytest.mark.parametrize("mode,event", [("failed", "response.failed"), ("cancelled", "response.cancelled"), ("raise", "response.failed")])
def test_terminal_failure_and_cancelled(tmp_path, mode, event):
    async def scenario():
        core, store, gateway, profile, client, model, app = await setup(tmp_path / "core.db", Model(mode=mode))
        a = await post(client)
        try:
            await core.drain()
        except RuntimeError:
            assert mode == "raise"
        response = await client.get(a["event_stream_url"])
        assert event in response.text and "private prompt" not in response.text
        await close(core, store, client)
    asyncio.run(scenario())

def test_explicit_core_cancel_late_output_fenced(tmp_path):
    async def scenario():
        core, store, gateway, profile, client, model, app = await setup(tmp_path / "core.db", Model(block=True))
        a = await post(client)
        await model.entered.wait()
        await core.cancel_turn(CancelTurnCommand(caller_context=CallerContext(logical_user_id=profile.user_id),
            conversation_ref=gateway.ref(profile, a["conversation_id"]), turn_id=TurnId(value=a["turn_ref"])))
        model.release.set(); await core.drain()
        events = (await client.get(a["event_stream_url"])).text
        assert "response.cancelled" in events and "answer" not in events
        await close(core, store, client)
    asyncio.run(scenario())

def test_telemetry_monotonic_bounded_private_and_true_delta_seam(tmp_path):
    async def scenario():
        core, store, gateway, profile, client, model, app = await setup(tmp_path / "core.db")
        gateway.token_streaming = True
        a = await post(client); await core.drain()
        events = (await client.get(a["event_stream_url"])).text
        assert "response.delta" in events and "MODEL_TOKEN" not in events
        row = next(iter(gateway.telemetry.records.values()))
        assert len(row) == 7 and list(row.values()) == sorted(row.values())
        serialized = json.dumps(gateway.telemetry.summaries())
        assert all(value is not None and 0 <= value <= 86400000 for name, value in gateway.telemetry.summaries()[0].items() if name != "turn_correlation")
        assert gateway.telemetry.summaries()[0]["turn_correlation"] == hashlib.sha256(a["turn_ref"].encode()).hexdigest()
        for private in (a["turn_ref"], a["conversation_id"], "yogurt", "private user text", "owner", "test persona", TOKEN):
            assert private not in serialized and private not in repr(gateway.telemetry.records)
        small = LatencyTelemetry(capacity=2)
        for i in range(5):
            small.checkpoint(gateway.ref(profile, a["conversation_id"]), TurnId(value=str(i)), "surface_received")
        assert len(small.records) == len(small.summaries()) == 2
        assert {row["turn_correlation"] for row in small.summaries()} == set(small.records)
        await close(core, store, client)
    asyncio.run(scenario())

def test_actual_asgi_sse_disconnect_does_not_cancel(tmp_path):
    async def scenario():
        core, store, gateway, profile, client, model, app = await setup(tmp_path / "core.db", Model(block=True))
        a = await post(client)
        sent = []
        first_event = asyncio.Event()
        async def send(message):
            sent.append(message)
            if b"response.started" in message.get("body", b""):
                first_event.set()
        async def receive():
            await first_event.wait()
            return {"type": "http.disconnect"}
        scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"}, "http_version": "1.1",
            "method": "GET", "scheme": "http", "path": a["event_stream_url"], "raw_path": a["event_stream_url"].encode(),
            "query_string": b"", "headers": [(b"authorization", ("Bearer " + TOKEN).encode())],
            "server": ("test", 80), "client": ("test", 1), "root_path": ""}
        await asyncio.wait_for(app(scope, receive, send), 1)
        assert model.cancelled == 0
        model.release.set(); await core.drain()
        assert "response.completed" in (await client.get(a["event_stream_url"])).text
        await close(core, store, client)
    asyncio.run(scenario())


def test_recovery_of_inflight_turn_replays_failure_without_rerun(tmp_path):
    async def scenario():
        path = tmp_path / "core.db"
        core, store, gateway, profile, client, model, app = await setup(path, Model(block=True))
        a = await post(client)
        await model.entered.wait()
        # Abrupt shutdown leaves processing facts for the standard Core recovery path.
        await core.stop(drain_timeout_seconds=0.001)
        await client.aclose(); await store.close()
        core, store, gateway, profile, client, model, app = await setup(path)
        retry = await post(client)
        assert retry["response_id"] == a["response_id"]
        assert retry["admission"] == "SAME_LOGICAL_TURN"
        assert "response.failed" in (await client.get(a["event_stream_url"])).text
        assert model.contexts == []
        await close(core, store, client)
    asyncio.run(scenario())


def test_broken_telemetry_does_not_change_core_outcome(tmp_path):
    async def scenario():
        core, store, gateway, profile, client, model, app = await setup(tmp_path / "core.db")
        class BrokenObserver:
            def checkpoint(self, *args):
                raise RuntimeError("observer unavailable")
        core._observer = BrokenObserver()
        a = await post(client); await core.drain()
        assert "response.completed" in (await client.get(a["event_stream_url"])).text
        await close(core, store, client)
    asyncio.run(scenario())


def test_ollama_final_adapter_is_bounded_local_and_has_no_fake_deltas(monkeypatch):
    from yogurt_native.model import LocalOllamaModel
    from yogurt_core.model.execution import ModelContext
    observed = []
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): return None
        def read(self, limit):
            assert limit == 2_000_001
            return b'{"message":{"content":"one final output"}}'
    class Opener:
        def open(self, request, timeout):
            assert request.full_url == "http://127.0.0.1:11434/api/chat"
            assert json.loads(request.data)["stream"] is False
            assert timeout == 120
            observed.append(request)
            return Response()
    monkeypatch.setattr("yogurt_native.model.urllib.request.build_opener", lambda *args: Opener())
    async def scenario():
        model = LocalOllamaModel("existing-model")
        context = ModelContext("persona", (), "hello", "native", 1024)
        output = [event async for event in model.run(context, None)]
        assert len(output) == 1 and output[0].text == "one final output"
        assert len(observed) == 1
    asyncio.run(scenario())


def test_crash_before_response_started_has_deterministic_terminal_retry(tmp_path):
    path = tmp_path / "core.db"
    marker = tmp_path / "admitted-turn.txt"
    script = r'''
import asyncio, os, sys
from pathlib import Path
from tests.native.test_gateway import setup, post
from yogurt_core_adapters.state_store_sqlite import SQLiteCoreStateStore
from yogurt_contracts.conversation.lifecycle import TurnState
async def crash_before_response(self, token, ref, turn_id, response_id):
    turn = await self.get_turn(ref, turn_id)
    assert turn.state == TurnState.INPUT_COMMITTED and turn.response_id is None
    Path(sys.argv[2]).write_text(turn_id.value, encoding="utf-8")
    os._exit(73)
SQLiteCoreStateStore.commit_response_started = crash_before_response
async def run():
    core, store, gateway, profile, client, model, app = await setup(Path(sys.argv[1]))
    await post(client)
    raise AssertionError("crash window was not reached")
asyncio.run(run())
'''
    crashed = subprocess.run([sys.executable, "-c", script, str(path), str(marker)],
                             cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True, timeout=10)
    assert crashed.returncode == 73, crashed.stderr
    admitted_id = marker.read_text(encoding="utf-8")

    async def scenario():
        from yogurt_contracts.conversation.lifecycle import AbortReason, TurnState
        core, store, gateway, profile, client, model, app = await setup(path)
        conversations = await gateway.conversations(profile)
        assert len(conversations) == 1
        ref = gateway.ref(profile, conversations[0].conversation_id)
        recovered = await store.conversation_turns(ref, profile.user_id)
        assert len(recovered) == 1
        turn = recovered[0]
        assert turn.turn_ref.turn_id.value == admitted_id
        assert turn.state == TurnState.ABORTED and turn.abort_reason == AbortReason.RECOVERY
        assert turn.response_id is None
        for _ in range(3):
            result = await client.post("/v1/native/turns", json=payload())
            assert result.status_code == 409
            assert result.json() == {"code": "TURN_TERMINATED_BEFORE_RESPONSE"}
        await core.drain()
        assert await store.conversation_turns(ref, profile.user_id) == recovered
        assert model.contexts == []
        # Read-only checks of real durable projections; no synthetic Response or Turn.
        assert store._connection.execute("SELECT COUNT(*) FROM turns").fetchone()[0] == 1
        assert store._connection.execute("SELECT COUNT(*) FROM responses").fetchone()[0] == 0
        await close(core, store, client)
    asyncio.run(scenario())


def test_active_response_start_wait_is_bounded_and_low_frequency(tmp_path, monkeypatch):
    async def scenario():
        core, store, gateway, profile, client, model, app = await setup(tmp_path / "core.db", Model(block=True))
        release_response = asyncio.Event()
        original_start = store.commit_response_started
        original_get = core.get_turn
        reads = 0
        async def delayed_start(*args):
            await release_response.wait()
            return await original_start(*args)
        async def counted_get(command):
            nonlocal reads
            reads += 1
            return await original_get(command)
        monkeypatch.setattr(store, "commit_response_started", delayed_start)
        monkeypatch.setattr(core, "get_turn", counted_get)
        start = time.monotonic()
        result = await client.post("/v1/native/turns", json=payload())
        elapsed = time.monotonic() - start
        assert result.status_code == 503 and result.json() == {"code": "CORE_UNAVAILABLE"}
        assert 1.9 <= elapsed < 3.0  # Two-second wait plus scheduling/HTTP overhead.
        assert 1 < reads <= 410  # 5 ms polling, not a sleep(0) hot loop.
        assert not model.contexts
        release_response.set()
        accepted = await post(client)
        assert accepted["admission"] == "SAME_LOGICAL_TURN"
        assert not model.release.is_set()  # Acceptance still precedes completion.
        model.release.set()
        await core.drain()
        assert len(model.contexts) == 1
        await close(core, store, client)
    asyncio.run(scenario())
