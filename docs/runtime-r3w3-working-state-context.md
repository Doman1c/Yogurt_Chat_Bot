# R3-W3: Core WorkingState context integration

W3 admits W1-derived conversation cognition into Core-backed model requests.
It is not Memory recall and does not change legacy OpenClaw/WeChat injection.

## Composition and authority

`ContextComposer` assembles distinct `ContextCompositionInputs`: the existing
historical `ContextProjection`, W1 `ConversationWorkingState`, and the frozen
`StoredTurn` (persona snapshot and current input). `ContextBudgetPolicy` owns
selection and final budgets. Additional explicit input types can later be added
at this seam without putting their acquisition in YogurtCore's retry loop.
No placeholder Memory input, Memory call or Memory evidence is added.

The existing two-input `ContextBudgetPolicy.compose` remains the history/persona
policy; `compose_inputs` adds the independently admitted material. History still
uses `projection.entries[-budget:]`, including its existing zero-budget behavior.
Neither projection entries nor PersonaSnapshot is mutated.

`ModelContext.working_state` is optional frozen `WorkingStateContextMaterial`:
derivation version, revision, through-turn reference and canonical JSON material.
The reference remains internal execution provenance, not Native HTTP state or
provider text. No context is written back to the fact ledger or a new database.

The JSON explicitly declares `authority=user_derived_context_data` and
`instructional=false`. This marker describes intent; it is not a guarantee that
a model cannot be influenced by malicious content. Protocol authority is enforced
by keeping it out of the system/persona message. LocalOllamaModel serializes the
approved string in a separate **user** message after history and before current
input. The adapter does not select fields or truncate material.

## Admission policy

- Only ACTIVE entries are eligible.
- Prior unresolved questions: at most 3; exclude any sourced from current Turn.
- Active threads: at most 3; require at least one prior source. A continued thread
  with both prior and current references is eligible.
- Prior focus: at most 2; exclude any sourced from current Turn.
- W1 order is preserved within each category; W1 collection bounds are unchanged.
- Priority is unresolved questions, threads, then focus. Candidate bounds apply
  before budgeting. Each whole value is tentatively added and skipped if the
  full serialized JSON would exceed **1200 Unicode characters**, including JSON
  overhead/escaping. Later smaller eligible values may still fit. No string is
  partially truncated and no provider-side budgeting is used.
- Canonical JSON uses sorted keys, compact separators, and Unicode text. No
  timestamps, random IDs, entry IDs or conversation identity enter the material.
- No admitted values means `None`, not an empty provider message. First-turn
  request structure remains persona plus current input.

Historical text can recur across history and different W1 categories; W3 does
not invent cross-category semantic deduplication. It specifically removes
current-Turn-only cognition. A repeated thread with prior sources is intentional.

## Source boundary, freezing and failures

`CoreStateStore` explicitly extends `WorkingStateSource`; there is no dynamic
capability check or silent fallback. SQLite uses the unchanged W1 fact reader.
InMemory supplies the same through-turn admission boundary from its authoritative
records, with admission/finalization mutation-order metadata under the existing
lock. This is ephemeral ordering metadata, not a second cognition authority.

Each logical response calls the composer once, before its first model attempt,
with the conversation and exact current TurnRef. Retries reuse the same frozen
ModelContext object. Later admissions cannot change that object. Projection read
errors propagate: CoreStateError read failures (except stale-authority fencing)
are wrapped as execution failures so existing Core failure convergence aborts
the response/Turn and releases its claim. Valid empty state is distinct from an
error. No new retry mechanism is introduced.

Package exports for Core ports and adapters are lazy, preserving public names
without eagerly loading Memory when using ephemeral Core. The existing SQLite
adapter still implements R3-B and imports its Memory contracts; that adapter is
not refactored. An import-denial test runs actual ephemeral Core turns with
Memory contracts, Core Memory, integrations, legacy and Native packages absent,
and forbids network connects during execution.

## Validation and frozen scope

W3 tests live in `tests/core`, `tests/native`, and `tests/architecture`, already
covered by the unchanged Python 3.9/3.10/3.11/3.12 CI matrix. They cover actual
SQLite/InMemory multi-turn execution, retry object equality and projection call
count, lifecycle filtering, boundary errors, history/persona preservation,
budgeting, deterministic output, mocked Ollama authority and package isolation.
The W1 architecture test's former blanket ban on all context wiring is replaced
by the W3 seam checks; W1 derivation isolation checks remain intact.

W1 semantic policy/contracts and W2 comparison are unchanged. Native client/API
contracts, legacy V4, B.7/B.8, production thresholds and Voice/Singing are untouched.
No production Memory or model calls are needed by the tests. W4 is now merged;
W5 is the final conformance stage. See [current track status](runtime-r3w-status.md).
R3-D remains BLOCKED by B.7/B.8; R3-D/R3-E are not implemented.
