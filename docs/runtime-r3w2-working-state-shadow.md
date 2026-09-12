# R3-W2: isolated Legacy V4 / Core WorkingState comparison

W2 is an offline analysis leaf. It runs no model or YogurtCore execution runtime,
has no public endpoint, and is not registered with Native, OpenClaw, Memory V5,
or a production request handler. W1 and Legacy V4 implementations are unchanged.

Run the sanitized fixtures from the repository root:

```sh
python -m integrations.working_state_shadow tests/fixtures/working_state_shadow.json
```

The CLI reads only the explicitly supplied fixture file (at most 4 MB) and writes
aggregate JSON to stdout. It takes no database, provider, service URL, telemetry,
or production configuration argument. Each case creates separate fresh Legacy
and Core SQLite databases inside its own temporary directory, then closes and
removes them. Existing databases are never used. Raw fixture text exists only in
the input and temporary replay storage. No generated artifact is committed.

Optional local review text export is deliberately omitted in W2. There is no
upload, raw-text logging, review-manifest option, or automatic promotion action.
CLI errors are bounded fixed messages; validation tracebacks containing fixture
content are suppressed. Summary and sample contracts reject extra raw fields.

## Replay policy (working-state-shadow-v1)

Fixtures contain ordered case IDs and turns. Each turn has bounded user text,
optional assistant text, an explicit FINALIZED/ABORTED/NONE outcome, and optional
observation/sample-delay offsets in seconds. The epoch is fixed at
`2026-01-01T00:00:00+00:00`; time cannot regress from a sample to the next turn.
All Legacy API calls receive explicit time. A sample delay advances only Legacy
expiry evaluation, not Core turn distance. Empty cases emit one sample (ordinal
zero). Nonempty cases emit one sample per turn after its specified outcome.

- User input is observed by the original Legacy engine and admitted through the
  actual Core SQLite store, producing authoritative isolated facts.
- FINALIZED commits supplied assistant segments and finalizes the primary Core
  response. Legacy observes that completed assistant text at the same timestamp.
- ABORTED may commit partial text to Core, then aborts its response. It is never
  treated as a completed Legacy assistant observation.
- NONE produces no response. After sampling, its Core turn is cancelled solely
  to release the fixture scheduling claim for another replay turn. No answer is
  invented. W1's pending question remains ACTIVE. ABORTED turns use the existing
  abort transition; FINALIZED turns use the existing completed transition.

Each sample calls the real `WorkingStateProjectionService` through that turn.
The runner does not reimplement normalization, IDs, lifecycle, or bounds. Core
internal fact metadata may contain Core-generated timestamps/event IDs; these
are neither compared nor exported and do not enter W1's deterministic result.

The Legacy adapter loads the repository's existing `conversation_state.py`
source as a standalone module to avoid its package's eager Memory/service imports.
It does not copy or replace classifiers. `list_states(now=...)` supplies the actual
TTL-applied state and `expired_fields`. Translation retains only booleans, counts,
state version and expiry flags; it discards raw identity/text immediately.

## Ownership and comparison matrix

| Legacy field | Core concept | Meaning |
| --- | --- | --- |
| current_topic | recent_focus | Structural presence, never string equality |
| open_threads | ACTIVE active_threads | Structural presence/counts, never topic matching |
| No equivalent | unresolved_items | CORE_ONLY_SIGNAL |
| dialogue_act, user_mood, interaction_mode, recent_actions, last_response_pattern | Not migrated | LEGACY_ONLY_UNMIGRATED |

`core_unresolved_count` counts ACTIVE questions; `core_resolved_count` counts
execution-resolved questions retained by W1. EXPIRED threads are reported
separately and do not count as active threads. Thread deltas are exact integer
bins of `Core ACTIVE count - Legacy open count`, sorted numerically.

Legacy-only mood/mode metrics mean non-neutral mood and non-default (`casual`)
mode. Dialogue-act presence includes its default for existing Legacy states;
an entirely empty conversation has no Legacy state and no present fields.

Policy flags report Legacy topic/thread TTL expiry while Core still has focus
or active threads, or Core expired threads coexisting with Legacy open threads.
These are observable policy differences, not proof that individual threads match
or that one policy caused an accuracy defect. Counts can overlap. They are not
Core failures, similarity scores, migration readiness or automatic promotion.
Semantic correctness still requires separately authorized labeled/manual review.

Sample IDs are SHA-256 over canonical JSON of shadow schema version, fixture-local
case ID, and ordinal. Raw case IDs and conversation identity are not exported.
Do not use names or real identities as fixture IDs. Duplicate case/sample IDs
are rejected. Aggregates record Legacy TTL config, shadow schema and W1 version;
unsupported versions fail validation. Ordering follows input fixture order, and
histograms/JSON keys are sorted. Identical fixtures/configuration produce identical
samples and canonical JSON across replay/restart and Python 3.9–3.12.

## Boundaries and validation

W2 tests exercise real implementations, presence partitions, response outcomes,
both expiry policies, W1 thread identity/source bounds, Unicode/long input,
privacy/error redaction, empty/rapid turns, scope isolation, provider absence,
and replay with network connections disabled. Architecture tests prohibit
production imports of W2, Core imports of Legacy, and Legacy imports of Core.

The existing CI matrix includes W2 tests and strict integration typing, along
with unchanged deterministic `test-v4-state` and `test-v4-runtime` scripts and
the existing Native, Memory, adapter, Voice/Singing regressions.

W2 sample counts are completely separate from B.7 `gate_evaluated_events`. W2
does not access production DBs, port 17621, thresholds, experiment counters or
B.8 criteria. W3/W4/W5 and R3-D/E are not implemented; R3-D remains NO-GO.
