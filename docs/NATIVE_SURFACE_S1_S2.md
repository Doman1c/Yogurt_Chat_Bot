# S1/S2 Native Surface foundation

Base: `origin/main` at `7f92f994209025432c494d9eedd3700af8f2cb23`.
No R3 stack dependency. Memory B.7/B.8 gates remain untouched.

## Run locally

Python 3.9–3.12; install `requirements-native.txt`. For tests install
`requirements-native-dev.txt`. HTTP dependencies are separate from Core requirements.

Supply these environment variables in the server process:

- `YOGURT_NATIVE_TOKEN`: random bearer token, at least 32 characters.
- `YOGURT_NATIVE_MAPPING_KEY`: independent random secret, at least 32 bytes.
  Preserve it with the Core database across restarts; changing it changes retry IDs.
- `YOGURT_NATIVE_DATABASE`: path to the authoritative Core SQLite database.
- `YOGURT_NATIVE_MODEL`: name of an already installed local Ollama model.
- `YOGURT_NATIVE_PERSONA_FILE`: UTF-8 server-side persona material file.
- Optional `YOGURT_NATIVE_PERSONA` / `YOGURT_NATIVE_USER`: defaults `yogurt` / `owner`.

Run `python -m yogurt_native`. It binds only `127.0.0.1:17622` with access logging
disabled. Environment secrets are never returned or logged. Use existing ignored
`secrets/`, `.env`, `*.local.json`, or `private/` locations for local material;
the launcher reads environment variables, not dotenv files automatically.
Do not pass secrets in URLs. No LAN/public/Tailscale configuration is enabled.

The bundled adapter calls only loopback Ollama `/api/chat` (port 11434), with
`stream=false`, bounded output and a timeout. Environment HTTP proxies are
bypassed for this private call. There is no Memory connection. No Ollama server
or model is installed or changed by this work.

## Wire contract

All endpoints require `Authorization: Bearer <token>`, including health and SSE.
Authentication runs before parsing request bodies or accessing Core. Duplicate
Authorization headers fail closed. Error responses contain a stable `code` only;
validation errors never echo submitted values.

`POST /v1/native/turns`:

```json
{
  "client_message_id": "phone-message-001",
  "text": "Hello",
  "surface_type": "native_ios",
  "device_instance_id": "phone-A"
}
```

Optional `conversation_id` continues an existing owned conversation. Omit it to
create a new one. `client_timestamp` is an advisory string of at most 64 characters.
Text is nonblank and at most 16000 characters, message ID at most 128 ASCII ID
characters, device ID at most 128 characters. Total body limit is 100000 bytes.
The surface enum is `native_ios`, `native_ipados`, `native_macos`.
Unknown fields, including identity/authority/provider/Turn IDs, are rejected.

A 202 returns `conversation_id`, opaque server `turn_ref`, Core `response_id`,
`event_stream_url`, and `admission` (`NEWLY_ADMITTED` or `SAME_LOGICAL_TURN`).
The gateway waits only for durable Core response creation (bounded to two seconds),
not model completion. A response-start timeout returns retryable 503 without
cancelling the admitted turn; retry the identical request. Polling uses a 5 ms
interval within the existing two-second bound. If the authoritative Turn is already
terminal without a Response (including recovery after a crash before ResponseStarted),
the same request deterministically returns non-retryable HTTP 409
`TURN_TERMINATED_BEFORE_RESPONSE`. No new Turn, synthetic Response, or model execution
is created. Only an intentional new user send should use a new client_message_id.

- `GET /v1/native/responses/{response_id}/events`: SSE projection of Core events.
- `GET /v1/native/conversations`: owned conversations for the configured persona.
- `GET /v1/native/conversations/{conversation_id}`: ordered Core Turn inputs and
  committed Response text/state, including partial and failed work.
- `GET /v1/native/health`: authenticated Core readiness and streaming capability.

Conversation reads are rebuildable projections of the existing Core fact journal;
there is no Surface database or chat ledger. Device metadata never changes user
identity. Both user and persona ownership are checked for reads and SSE.

## Retry and replay rules

Trusted ingress derives opaque IDs with HMAC-SHA256 under its stable secret,
scoped by configured persona/user, conversation and client_message_id. New
conversation IDs are also stable for retries of an omitted-conversation request.
The client key is never used directly as Core TurnId. Core's frozen idempotency
key remains `ConversationRef + TurnId`.

The exact validated payload, including advisory fields and whether conversation_id
was omitted, is hashed into the principal binding reference carried by the existing
Core fingerprint. Changed payloads fail with 409 `CLIENT_MESSAGE_CONFLICT`, even
for equivalent Unicode or newline-normalized text. Retry the original payload
without adding the returned conversation ID; use it for subsequent new messages.
Transport body key ordering is immaterial. No external mapping ledger is needed.
The configured principal/resolution and mapping secret must remain stable for
retries. Bearer rotation can retain that mapping. A busy conversation returns 409
`CONVERSATION_BUSY`; retry the same request after the prior turn is terminal.

SSE `id` is the authoritative response event sequence. `Last-Event-ID: N` replays
only events after N. A terminal stream then closes. New connections replay from
zero. Disconnecting the consumer only closes its iterator; it does not cancel Core.
A cursor beyond the current sequence waits for later events or closes at terminal.

Events are `response.started`, `response.text` (bundled final-text model adapter),
`response.completed`, `response.failed`, or `response.cancelled`. The started event
reports `MODEL_TOKEN_STREAMING_NOT_YET_AVAILABLE` for the bundled adapter.
There is no timer/character slicing. A genuine streaming ModelPort may set
`NativeGateway(..., token_streaming=True)` to project committed text segments as
`response.delta`; Core sequence/lifecycle remains authoritative. Segment boundaries
are Core commit boundaries, not necessarily individual model tokens.

## Latency and limitations

A transport-neutral optional Core observer records monotonic checkpoints:
`surface_received`, `core_admission_started`, `core_admitted`, `model_started`,
`first_model_output`, `model_finished`, `response_terminal`. Surface receipt is
captured at HTTP entry. Model first output is recorded at the ModelPort boundary;
for the final-only Ollama adapter TTFT equals time until final output, not token TTFT.
Across Core retries model_started/first output retain their first observations,
while model_finished tracks the last attempt finish. Missing stages remain absent
(e.g. failure before any output); they are never fabricated.

`LatencyTelemetry.summaries()` exports at most 1024 summaries by default:
`surface_to_core_ms`, `core_pre_model_ms`, `model_ttft_ms`, `model_generation_ms`,
`total_ms`, plus `turn_correlation` (SHA256 of the opaque Core TurnId).
No raw TurnId or conversation/user/persona identity is exported. Timing values are
clamped to [0, 86400000] ms, or null for unobserved pairs.
Records contain only opaque hashed Turn correlation and monotonic numbers, never
raw input, prompts, memory or private identity. No latency audit is persisted.
Capacity eviction and restart may lose timings, never Core facts. Observer failure
cannot alter Core execution. This local callable is the telemetry export seam;
no public metrics endpoint is added.

The local Ollama non-streaming request runs in a worker thread. Explicit Core
cancellation fences late output; it cannot interrupt that blocking HTTP request.
The two-second response-start timeout and single-writer Core are deliberate S2
limits. Conversation reads currently return the full local projection; pagination,
stream heartbeats, native UI and external exposure are follow-up work.

## Validation

Run Native tests first, then R1/R2 contracts/conformance/architecture/Core tests:

```text
python -m pytest -q tests/native
python -m pytest -q tests/contracts tests/conformance tests/architecture tests/core
python -m mypy --strict yogurt_contracts yogurt_core_domain yogurt_conformance yogurt_core yogurt_core_adapters yogurt_native
git diff --check
```

CI runs Native tests in the existing Python 3.9/3.10/3.11/3.12 matrix. Tests use a
controlled ModelPort and real SQLite facts, including restart, concurrent duplicate
POST, replay, actual ASGI disconnect, failure/cancellation, cross-device context,
anti-forgery, timing privacy, and optional OpenClaw/WeChat import blockers.
A live Ollama performance comparison requires the operator's configured model
and persona; deterministic tests do not claim real-model latency measurements.

## Local validation record (2026-09-10)

- Native suite first: 29 passed.
- Contracts/conformance/architecture/Core regression: 140 passed.
- Combined matrix: 169 passed on each of CPython 3.9.25, 3.10.21, 3.11.16,
  and 3.12.14. Strict mypy: 137 source files passed on each runtime.
- `git diff --check`: passed.
- Additional main Memory regression: 65 passed, 3 failed, 5 errors on the local
  Windows Python 3.14 environment. The failing tests were independently rerun
  against the untouched main checkout: the same three failing test names and five
  setup errors reproduced (24 passed in that focused baseline run).
  Fixed-corpus hash failures come from Windows CRLF checkout conversion: replacing
  CRLF with LF in memory matches the origin/main Git blobs and the hard-coded
  expected SHA256 values. Other failures are WinError 32 during cleanup of open
  temporary SQLite databases. Neither fixtures nor Memory gates were changed.
- No live Ollama model invocation/performance result is claimed. The adapter's
  local endpoint, final-only behavior, and output bound are tested with a controlled
  HTTP opener; real Core admission and durable SSE behavior use a controlled ModelPort.

Final hardening validation: 31 Native tests and 140 Core-related regression tests
passed on each of Python 3.9/3.10/3.11/3.12; strict mypy passed for 137 source files.
The crash-window test exits a subprocess after durable admission and before
ResponseStarted, then uses standard R2 recovery. Three identical retries return
409 with the same terminal Turn, zero model invocations and zero Responses.
