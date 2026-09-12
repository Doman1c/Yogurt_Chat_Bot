# Native S2.5: real model streaming

Base: main `9095839664eecd0671ba53b93f3efed446dc0eaa`.

The Native launcher enables real Ollama streaming. Clients still use only the
authenticated loopback Gateway. The model adapter posts `stream=true` to
`http://127.0.0.1:11434/api/chat` using async HTTPX with environment proxies disabled,
no redirects, and a 120-second transport timeout. HTTPX is now a Native runtime
dependency; Core dependencies do not change.

NDJSON records must have a boolean `done`; nonterminal records require a message
with string content. Empty string content is valid. A terminal `done=true` record
may omit the message and may include final content, which is processed exactly
once. EOF before done, malformed JSON/UTF-8, invalid content, provider error objects,
and HTTP failures become sanitized FAILED attempt terminals. No provider exception
text is returned to clients. Data after terminal done is not consumed.

Bounds: 65536 bytes per NDJSON record, 8000000 bytes per decoded HTTP stream, and
2000000 generated text characters. UTF-8 and NDJSON record boundaries may span
transport buffers. The adapter buffers at most one record plus a coalescing prefix;
there is no unbounded producer queue or background stream after consumer closure.

Coalescing uses only real provider text in original order. It flushes at 80 characters,
on a newline, or at punctuation after at least 40 characters. Final residual text
flushes on done. These boundaries are deterministic across transport fragmentation;
there is no timer, word splitting animation, or fabricated text. Uncommitted residual
text is discarded on failure. Core preserves every whitespace character (the old
per-delta strip was removed), so successful committed segment concatenation exactly
matches provider-generated content, including leading/trailing spaces and newlines.

Each coalesced VisibleTextDelta enters the existing durable segment commit. Only
Core's committed ResponseEventSequence is projected to `response.delta` SSE events.
ResponseStarted/Finalized, sequence numbers, Last-Event-ID, reconnect, idempotency,
and the one-logical-response rule remain unchanged. SSE disconnect never cancels
Core. The generic Gateway still supports non-streaming injected ModelPorts; the
Native launcher explicitly declares this adapter's token-streaming capability.

Provider failure before any committed prefix can use Core's existing second attempt.
After a committed prefix Core aborts the same response without another generation.
Explicit cancellation interrupts the pending async read and closes the HTTP response.
Core's authority and terminal-state checks fence any in-flight late chunk; explicit
iterator closure also closes provider resources on commit rejection/authority loss.
There is no change to recovery or response authority.

WorkingState context composition and authority stay unchanged. The already composed
immutable ModelContext is reused throughout streaming and retry attempts. Historical
WorkingState material remains a user-role message, never system authority. ModelPort
first-output timing now measures first coalesced output; it is not raw provider-token
TTFT. No Memory/Voice/Singing/OpenClaw/WeChat behavior or B.7/B.8 gate is modified.

Validation covers mocked provider NDJSON, live incremental delivery before provider
completion, a blocked durable commit, exact whitespace/Unicode reconstruction,
fragmentation, residual flush, parser bounds/failures, both prefix retry cases,
cancellation/authority-loss closure, replay and both SQLite/InMemory stores. Tests
use no installed Ollama model and claim no live hardware performance measurement.

## Validation record (2026-09-12)

- Python 3.9.25, 3.10.21, 3.11.16, 3.12.14: Native 59 passed per version.
- Contracts/conformance/architecture/Core/integration: 321 passed per version
  (379 combined before the final extra token-coalescing test, then final Native
  suite rerun on every version).
- Strict mypy: 163 source files passed on all four versions, including integrations.
- Full Python regression on Windows 3.12, including social_layer, Voice, Singing,
  Mouth and VoiceSession: 569 passed, 3 failed, 5 errors. All three failing test
  names and five setup errors reproduce on an independent untouched checkout of
  the exact base `9095839`: Memory corpus CRLF hash mismatch and WinError 32 on
  temporary SQLite cleanup. The focused baseline reproduction had 24 passed,
  3 failed and 5 errors. No Memory fixture, hash, or gate changes were made.
- All nine CI shell regression entry points were attempted under Git Bash.
  Four passed: sticker-explicit-fallback, sticker-v21, v3-runtime and
  v3-sticker-bubble-order. Five failed and reproduced on the exact base checkout:
  openclaw-adapter and v4-state (UTF-8 decoding of local script output),
  sticker-auto-delivery and v4-runtime (bridge_spawn_failed for the shell test
  executable on Windows), and v3 (exit 2 after its seven Python tests and splitter
  check passed). Linux GitHub CI remains the platform-wide confirmation step.
- `git diff --check`: passed. No live Ollama service was required or contacted.
