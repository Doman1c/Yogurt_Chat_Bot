# R3-W1: Core-owned WorkingState projection

`WorkingStateProjectionService.project(conversation_ref, through_turn_ref)` is a
private, read-only Core API. It does not participate in execution, prompts,
Native responses, ContextProjection, or provider calls. No public endpoint,
WorkingState table, mutation command, cache, or telemetry was introduced.

## Authority and boundary

The SQLite source reads an allowlist of existing committed facts:
`conversation_created`, `turn_input_committed`, `turn_processing`, and
`response_finalized`. Only text input parts supply excerpts. A finalized response
resolves a question only if its ID equals that turn's primary response ID.
Partial segments, aborted responses, attempts, and temporary output cannot
resolve it. Resolution denotes execution completion, not answer correctness.

Admission order in the append-only ledger defines turn order. Turn IDs are not
sorted, timestamps are not compared, and relational table insertion order is
not consulted. Physical fact row numbers are not exposed or used in revision
arithmetic. The source is read synchronously under the store lock in one cursor
snapshot, with no awaits between facts.

An explicit boundary is required for any nonempty conversation. Unknown turns,
cross-conversation boundaries, and missing conversations fail closed. `None`
is valid only for an existing empty conversation. The prefix stops immediately
before the next admission in the same full conversation identity, excluding all
subsequent facts even if they concern an older turn. While the requested turn is
still the last admitted turn, its response lifecycle can advance the projection.
This is a through-turn boundary, not a per-response context freeze or an immutable
snapshot of an in-flight turn; those integrations are outside W1.

## Versioned deterministic baseline

Only `working-state-v1` is supported. Any other requested version is rejected;
there is no fallback or cached projection to reuse across versions. Future rule
changes require a new implementation/version boundary.

- Normalize with Unicode NFC, then split/join whitespace to one ASCII space.
  CRLF/LF, repeated whitespace and decomposed Unicode normalize consistently.
  Case and Chinese punctuation are preserved. Values truncate to 160 codepoints.
- Revision is `2 * admitted_turn_count + finalized_primary_response_count` in
  the prefix. Empty revision is zero. Every emitted entry's derived revision is
  the current state revision. Repeated reads and rebuilds do not increment it.
- Recent focus: newest 3 nonempty user text excerpts, newest first.
- Questions: newest 5 explicit `?`/`？` inputs, newest first; ACTIVE until the
  corresponding primary response finalizes, then RESOLVED. No semantic NLP.
- Threads: full normalized text is the equality key, not its truncated excerpt.
  Retain the newest 5 distinct keys, ordered by last occurrence, newest first.
  Retained keys preserve first-source entry identity and the last 3 source refs.
  More than 3 admitted turns of inactivity yields EXPIRED. Recurrence reactivates
  a retained thread; an evicted thread starts a new first-source identity.
- Entry IDs hash canonical JSON of version, full conversation identity, kind,
  full normalized text and first source TurnRef. No random or clock input.

Collections and source refs are frozen ordered tuples with contract-enforced
bounds. Historical input scanning is on demand; W1 prioritizes reproducibility
over incremental performance. Excerpts remain private and are never logged.

## Rebuild and independence

No derived materialization needs recovery. Closing/reopening the store or
deleting/rebuilding its relational projections from the facts produces exactly
the same WorkingState for the same version and source prefix, including IDs,
ordering, lifecycle, source refs and revisions (frozen acceptance #74).

Core package exports load the application lazily, preserving `YogurtCore`'s
public import while allowing pure projections to import without optional Memory
ports. WorkingState modules import no Memory, Social Layer, Native, model or
external service implementation. Existing legacy V4, ContextProjection,
ModelContext and Native Surface behavior remains unchanged.

Those integrations were outside W1's original scope. For current W1–W5 status,
see [R3-W final ownership status](runtime-r3w-status.md). W3 now admits WorkingState
as user-derived context; R3-D/E and production Memory recall remain unimplemented.
