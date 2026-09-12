"""Provider-origin NDJSON through real Core stores and authoritative SSE."""
import asyncio
import json
from datetime import datetime, timezone

import httpx
import pytest

from tests.native.test_gateway import setup, close, post, payload
from yogurt_contracts.execution import ExecutionAttemptSnapshot
from yogurt_contracts.foundation.identifiers import ExecutionAttemptId, ResponseId, TurnId
from yogurt_contracts.foundation.time import UtcInstant
from yogurt_core.application.commands import CallerContext, CancelTurnCommand
from yogurt_core.model.events import AttemptTerminal, AttemptTerminalKind, VisibleTextDelta
from yogurt_core.model.execution import ModelContext
from yogurt_core_adapters.state_store_memory import InMemoryCoreStateStore
from yogurt_native.model import LocalOllamaModel, CHUNK_CHARS, MAX_LINE_BYTES


def attempt():
    return ExecutionAttemptSnapshot(execution_attempt_id=ExecutionAttemptId(value="attempt"),
        response_id=ResponseId(value="response"), binding_snapshots=(),
        started_at=UtcInstant(value=datetime.now(timezone.utc)), provider_applied_config_revision="test")


def line(text="", done=False):
    return (json.dumps({"message": {"content": text}, "done": done}, ensure_ascii=False) + "\n").encode()


class Bytes(httpx.AsyncByteStream):
    def __init__(self, blocks, gate=None):
        self.blocks, self.gate = blocks, gate
        self.closed = False
        self.waiting = asyncio.Event()

    async def __aiter__(self):
        for index, block in enumerate(self.blocks):
            if index == 1 and self.gate is not None:
                self.waiting.set()
                await self.gate.wait()
            if isinstance(block, Exception):
                raise block
            yield block

    async def aclose(self):
        self.closed = True


class Provider(LocalOllamaModel):
    def __init__(self, streams):
        self.streams = iter(streams)
        self.requests, self.contexts, self.context_snapshots = [], [], []
        def handle(request):
            assert str(request.url) == "http://127.0.0.1:11434/api/chat"
            body = json.loads(request.content)
            assert body["stream"] is True
            self.requests.append(body)
            return httpx.Response(200, stream=next(self.streams))
        super().__init__("existing-model", transport=httpx.MockTransport(handle))

    async def run(self, context, attempt):
        self.contexts.append(context)
        self.context_snapshots.append(repr(context))
        stream = super().run(context, attempt)
        try:
            async for event in stream:
                yield event
        finally:
            await stream.aclose()


@pytest.fixture(params=["sqlite", "memory"])
def store_kind(request, monkeypatch):
    if request.param == "memory":
        class Memory(InMemoryCoreStateStore):
            async def close(self):
                pass
        monkeypatch.setattr("tests.native.test_gateway.SQLiteCoreStateStore", lambda path: Memory())
    return request.param


def test_incremental_durable_before_visible_and_replay(tmp_path, store_kind, monkeypatch):
    async def run():
        provider_release = asyncio.Event()
        source = [" leading " + "x" * 83, "  中🙂\n", "tail  "]
        wire = Bytes([line(source[0]), line(source[1]) + line(source[2], True)], provider_release)
        model = Provider([wire])
        core, store, gateway, profile, client, _, app = await setup(tmp_path / "core.db", model)
        gateway.token_streaming = True
        entered_commit, release_commit = asyncio.Event(), asyncio.Event()
        original_commit = store.commit_segment_commit
        async def delayed_commit(*args):
            entered_commit.set()
            await release_commit.wait()
            return await original_commit(*args)
        monkeypatch.setattr(store, "commit_segment_commit", delayed_commit)
        try:
            accepted = await asyncio.wait_for(post(client), 1)
            rid = ResponseId(value=accepted["response_id"])
            stream = gateway.events(profile, rid.value)
            assert "response.started" in await stream.__anext__()
            visible = asyncio.create_task(stream.__anext__())
            await entered_commit.wait()
            assert (await store.get_response(rid)).segments == ()
            assert not visible.done()  # Provider text cannot escape before commit.
            release_commit.set()
            first = await asyncio.wait_for(visible, 1)
            assert "response.delta" in first
            response = await store.get_response(rid)
            assert len(response.segments) == 1 and response.events[-1].event_sequence == 2
            assert response.segments[0].text == "".join(source)[:CHUNK_CHARS]
            assert not provider_release.is_set()
            await stream.aclose()  # SSE disconnect is not provider/Core cancellation.
            assert not wire.closed
            provider_release.set()
            await core.drain()
            response = await store.get_response(rid)
            assert "".join(segment.text for segment in response.segments) == "".join(source)
            assert len(response.segments) >= 3
            assert [s.sequence for s in response.segments] == list(range(1, len(response.segments) + 1))
            assert all(len(s.text) <= CHUNK_CHARS for s in response.segments)
            assert response.segments[-1].text == "tail  "
            full = (await client.get(accepted["event_stream_url"])).text
            resumed = (await client.get(accepted["event_stream_url"], headers={"Last-Event-ID": "2"})).text
            assert full == "".join([event async for event in gateway.events(profile, rid.value)])
            assert resumed == "".join([event async for event in gateway.events(profile, rid.value, 2)])
            ids = [int(row[4:]) for row in full.splitlines() if row.startswith("id: ")]
            assert ids == list(range(1, len(response.events) + 1))
            assert [int(row[4:]) for row in resumed.splitlines() if row.startswith("id: ")] == ids[2:]
            assert "response.completed" in resumed and wire.closed
            assert len(model.requests) == 1
        finally:
            release_commit.set(); provider_release.set()
            await close(core, store, client)
    asyncio.run(run())


@pytest.mark.parametrize("bad", [b"not json\n", b"[]\n", b'{"done":false}\n',
    b'{"message":{"content":3},"done":false}\n', b'{"message":{"content":"x"},"done":"true"}\n',
    b'{"error":"private provider error"}\n', line("uncommitted residual"), b"x" * (MAX_LINE_BYTES + 1),
    b"\xff\n", b'{"message":{"content":"\\ud800"},"done":true}\n'], ids=["malformed", "array", "missing-content", "invalid-content", "invalid-done", "provider-error", "eof", "oversized", "invalid-utf8", "surrogate"])
def test_invalid_or_unterminated_stream_is_typed_failure(bad):
    async def run():
        wire = Bytes([bad])
        model = Provider([wire])
        events = [event async for event in model.run(ModelContext("persona", (), "text", "native", 1024), attempt())]
        assert len(events) == 1 and isinstance(events[0], AttemptTerminal)
        assert events[0].kind == AttemptTerminalKind.FAILED
        assert events[0].reason == "OLLAMA_STREAM_FAILURE" and wire.closed
    asyncio.run(run())


@pytest.mark.parametrize("prefix", [False, True])
def test_prefix_aware_retry_and_frozen_working_state(tmp_path, store_kind, prefix):
    async def run():
        initial = Bytes([line("prior answer", True)])
        failed = Bytes([line("x" * CHUNK_CHARS if prefix else "buffered"), b"malformed\n"])
        retry = Bytes([line(" retry result  ", True)])
        model = Provider([initial, failed, retry])
        core, store, gateway, profile, client, _, app = await setup(tmp_path / "core.db", model)
        try:
            first = await post(client); await core.drain()
            second = await post(client, payload("m2", conversation_id=first["conversation_id"]))
            await core.drain()
            response = await store.get_response(ResponseId(value=second["response_id"]))
            text = "".join(s.text for s in response.segments)
            assert len(model.requests) == (2 if prefix else 3)
            assert len(response.attempts) == (1 if prefix else 2)
            assert text == ("x" * CHUNK_CHARS if prefix else " retry result  ")
            assert response.state.value == ("aborted" if prefix else "finalized")
            context = model.contexts[1]
            assert context.working_state is not None
            assert repr(context) == model.context_snapshots[1]
            assert model.requests[1]["messages"][-2] == {"role": "user", "content": context.working_state.material}
            assert all(context.working_state.material != m["content"] for m in model.requests[1]["messages"] if m["role"] == "system")
            if not prefix:
                assert model.contexts[1] is model.contexts[2]
                assert model.requests[1] == model.requests[2]
        finally:
            await close(core, store, client)
    asyncio.run(run())


def test_cancel_stops_reading_and_fences_late_text(tmp_path, store_kind):
    async def run():
        release = asyncio.Event()
        wire = Bytes([line("prefix\n"), line("late text", True)], release)
        model = Provider([wire])
        core, store, gateway, profile, client, _, app = await setup(tmp_path / "core.db", model)
        try:
            accepted = await post(client)
            await wire.waiting.wait()
            await core.cancel_turn(CancelTurnCommand(caller_context=CallerContext(logical_user_id=profile.user_id),
                conversation_ref=gateway.ref(profile, accepted["conversation_id"]), turn_id=TurnId(value=accepted["turn_ref"])))
            await asyncio.wait_for(core.drain(), 1)
            assert wire.closed
            release.set()
            response = await store.get_response(ResponseId(value=accepted["response_id"]))
            assert "".join(s.text for s in response.segments) == "prefix\n"
            assert response.abort_reason.value == "cancelled"
            assert len(model.requests) == 1 and not model._cancellations
        finally:
            release.set()
            await close(core, store, client)
    asyncio.run(run())


def test_utf8_fragmentation_and_deterministic_coalescing():
    async def run(blocks):
        model = Provider([Bytes(blocks)])
        return [e.text async for e in model.run(ModelContext("", (), "", "native", 1024), attempt())]
    text = " " + "中🙂" * 50 + ".\n "
    wire = line(text, True)
    chunks = asyncio.run(run([wire]))
    assert chunks == asyncio.run(run([bytes([byte]) for byte in wire]))
    assert "".join(chunks) == text and len(chunks) > 1


def test_authority_loss_fences_next_chunk_and_closes_provider(tmp_path, store_kind):
    async def run():
        release = asyncio.Event()
        wire = Bytes([line("prefix\n"), line("late text\n", True)], release)
        model = Provider([wire])
        core, store, gateway, profile, client, _, app = await setup(tmp_path / "core.db", model)
        try:
            accepted = await post(client)
            await wire.waiting.wait()
            rid = ResponseId(value=accepted["response_id"])
            before = await store.get_response(rid)
            await store.release_authority(core._require_token())
            core._token = None  # Process no longer owns the store; shutdown must not release twice.
            release.set()
            await core.drain()
            assert await store.get_response(rid) == before
            assert wire.closed and not model._cancellations
            assert len(model.requests) == 1
        finally:
            release.set()
            await close(core, store, client)
    asyncio.run(run())


@pytest.mark.parametrize("bound", ["MAX_STREAM_BYTES", "MAX_TEXT_CHARS"])
def test_stream_total_bounds_are_fail_closed(monkeypatch, bound):
    monkeypatch.setattr("yogurt_native.model." + bound, 10)
    async def run():
        model = Provider([Bytes([line("x" * 20, True)])])
        events = [e async for e in model.run(ModelContext("", (), "", "native", 1024), attempt())]
        assert len(events) == 1 and events[0].kind == AttemptTerminalKind.FAILED
    asyncio.run(run())


def test_terminal_done_without_content_and_no_trailing_newline():
    async def run():
        model = Provider([Bytes([line("residual  "), b'{"done":true}'])])
        events = [e async for e in model.run(ModelContext("", (), "", "native", 1024), attempt())]
        assert [e.text for e in events] == ["residual  "]
    asyncio.run(run())


def test_many_provider_tokens_coalesce_without_text_changes():
    async def run():
        tokens = [" a", "b ", "中🙂", "  "] * 40
        wire = b"".join(line(token) for token in tokens) + line(done=True)
        model = Provider([Bytes([wire])])
        chunks = [e.text async for e in model.run(ModelContext("", (), "", "native", 1024), attempt())]
        assert "".join(chunks) == "".join(tokens)
        assert len(chunks) == (len("".join(tokens)) + CHUNK_CHARS - 1) // CHUNK_CHARS
        assert len(chunks) < len(tokens) / 10
    asyncio.run(run())
