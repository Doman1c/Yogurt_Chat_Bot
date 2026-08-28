# Yogurt Runtime Contracts & Execution Semantics RFC v0.1

- Status: Architecture gate proposal for acceptance
- Scope: R0.5; semantic contract only, no protobuf/transport/backend implementation
- Depends on: [Yogurt Modular Runtime RFC v0.2](YOGURT_MODULAR_RUNTIME_RFC_V0_2.md)
- Normative language: **MUST**, **MUST NOT**, **SHOULD**, and **MAY** are normative.

## 1. Definitions and authoritative writers

| Concept | Definition and authoritative writer | Commit / privacy / version rule |
| --- | --- | --- |
| `ExternalPrincipal` | Surface-authenticated external subject; Surface writes authentication evidence. | Opaque/provider-scoped; no raw credentials cross domain. |
| `IdentityResolution` / `LogicalUser` | Resolver maps principal to Yogurt logical user. Identity Resolver is sole writer. | Revisioned; Surface cannot self-assert owner. |
| `ConversationRef` | `{persona_id,user_id,conversation_id}` stable conversation namespace. Core writes conversation state. | Opaque, bounded, privacy-safe `conversation_id`; contract-major versioned. |
| `TurnRef` | `{conversation_id,turn_id,parent_turn_id?,sequence?}` one logical interaction. Core writes state. | `turn_id` is never a trace ID. |
| `Response` / `Segment` | Normalized assistant response / ordered semantic part. Core is writer. | Core reserves immutable `response_id` for the semantic stream; only commit makes its content authoritative. Segment has `segment_id`. |
| `PresentationReceipt` | Surface/Voice observed delivery/presentation fact. Presenting layer writes receipt. | It never rewrites response content or commit. |
| `Operation` | A requested side effect keyed by `operation_id`. Executing provider writes outcome. | One ID means one logical request; no exactly-once claim. |
| `CommittedConversationObservation` | Immutable fact sent to Memory Runtime. Core writes fact; Memory writes memory policy/state. | No raw drafts or raw provider output. |

Every request carries protocol/schema versions, `CallerIdentity`, deadline, correlation trace, and required domain IDs. Unknown security-critical values fail closed. `trace_id` is correlation only.

## 2. Input, turn, and response lifecycle

```text
RECEIVED → INPUT_COMMITTED → PROCESSING → RESPONSE_STREAMING
                                            │
                       ┌────────────────────┴───────┐
                       ▼                            ▼
             RESPONSE_COMMITTED → PRESENTING → COMPLETED
                       │
                       └── (presentation facts arrive independently)

Terminal from an uncommitted active turn: CANCELLED | ABORTED | FAILED | RECOVERY_ABORTED
```

`RECEIVED` is validated but not yet conversational state. Input becomes an authoritative conversation input only at `INPUT_COMMITTED`, after identity resolution, conversation isolation, validation, dedupe/admission, and its immutable `TurnSnapshot` have succeeded. A final STT hypothesis must be normalized and admitted before that transition. Partial STT revisions are not turn inputs.

`PROCESSING` includes context/model/tool work. Provider tokens and deltas remain draft. When Core begins a semantic response stream, it reserves a stable `response_id`; ID reservation is correlation only, not a content commit. `RESPONSE_STREAMING` publishes only normalized, ordered semantic segments, and all such content is still draft. At `RESPONSE_COMMITTED`, Core atomically accepts the complete normalized response assembly for that ID. Before that point, neither conversation history, durable Memory observation, TTS authoritative transcript, nor Surface permanent reply may treat output as authoritative.

At `RESPONSE_COMMITTED`, Core appends the final assembled response to authoritative conversation history and emits `AgentResponseCommitted`. It MAY subsequently receive presentation facts, but no fact can retract/alter the committed response. Core enters `PRESENTING` only when it requests presentation; it reaches `COMPLETED` when all required presentation work has a terminal receipt or the policy deadline expires. Presentation failure does not reverse response commit. `PRESENTED` content is never logically rolled back; a correction is a later committed response.

`CANCELLED` means work stopped before response commit where termination was observed. `ABORTED` means an active turn ended without a usable commit due to a non-recoverable execution failure. `FAILED` represents a request rejected/failed under a defined contract. `RECOVERY_ABORTED` is reserved for active work found after Core restart. No terminal state may regress.

## 3. Generated, committed, presented, persisted

These are four facts, not aliases:

| Fact | Meaning | Writer / transition |
| --- | --- | --- |
| Generated | Provider/model produced candidate material. | Provider; draft only. |
| Committed | Core accepted normalized input/response into authoritative conversation state. | Core at `INPUT_COMMITTED` or `RESPONSE_COMMITTED`. |
| Presented | A presentation layer observed a specific extent rendered/delivered. | Surface/Voice receipt; may be partial. |
| Persisted | The relevant domain writer durably stored its own state. | Core/Memory/provider independently; not implied by presentation. |

Commit does not mean a user saw output. Send acceptance does not mean delivery; delivery does not mean a WeChat user read it. A durable response may exist even if no content was presented. Conversely, an interrupted voice can have a partial presentation of a fully committed response.

## 4. Semantic output and presentation contracts

Raw model internals MUST NOT cross the Core boundary. The public semantic stream is:

```text
AgentResponseStarted(response_id, TurnRef)
AgentOutputSegment(response_id, segment_id, sequence, text, language, affect,
                   interruptibility, is_final)
AgentResponseCommitted(response_id, authoritative_segments, assembly_hash)
AgentResponseCompleted(response_id)
AgentResponseAborted(TurnRef, reason)
ToolCallRequested(operation_id, class, normalized request)
ToolCallCompleted(operation_id, outcome)
```

`response_id` is reserved before semantic streaming so every `AgentOutputSegment` has stable `{response_id,segment_id,sequence}`. This MUST NOT be interpreted as `RESPONSE_COMMITTED`: draft segments cannot become history, durable memory, an authoritative TTS transcript, or a permanent Surface reply. Only `AgentResponseCommitted` makes their final ordered assembly authoritative. A realtime consumer MAY render draft segments only under an explicit cancellable presentation policy and MUST issue receipts identifying that they were not yet committed at the time of presentation. `affect`, `language`, and `interruptibility` are segment-level fields so Voice/Embodiment can act on “嗯……” differently from the next sentence. No chain-of-thought, reasoning, raw tokens, tool grammar, or provider JSON is permitted.

`PresentationReceipt` is:

```text
{ response_id, segment_id?, presentation_target, status, presented_extent,
  observed_at, external_reference?, operation_id? }
```

`ACCEPTED` means a presentation provider accepted work; `DELIVERED` means the provider/platform confirmed delivery; `PRESENTED` means the layer can actually prove rendering; `PARTIALLY_PRESENTED` records a real prefix/extent; `INTERRUPTED` records stopped presentation; `FAILED` records failure. `presented_extent` is text extent, audio position, or platform message subset as contractually appropriate. Voice reports `SpeechProgress(response_id, segment_id, audio_position_ms, text_extent?)`; WeChat reports platform-confirmed delivery and external message references, never read status absent a real platform signal.

## 5. Memory observation boundary

Core does not extract or score long-term memory. Only after an input/response is committed, Core MAY emit:

```text
CommittedConversationObservation {
  ConversationRef, TurnRef, response_id?, speaker, committed_content,
  presentation_facts, provenance, timestamp
}
```

Core sends facts once using an operation ID and records uncertain delivery using the operation contract. Memory Runtime owns candidate extraction, filters, dedupe, score, retrieval, index, lifecycle, and persistence. Raw model drafts, cancelled assistant drafts, uncommitted response/tool result, provider reasoning, and raw audio MUST NOT become durable memory. A committed response with partial presentation yields facts “committed” plus “partial presentation”; Memory policy alone decides its lasting value.

## 6. Side effects, outcomes, retries, cancellation

All side-effect requests require an `operation_id` distinct from turn/response/trace IDs. Equal operation ID means equal logical request and request fingerprint; a conflicting reuse is `FAILED/IDEMPOTENCY_CONFLICT`. Providers SHOULD durably deduplicate and expose query/recovery where feasible; they MAY use provider-native idempotency. Yogurt guarantees neither universal exactly-once nor blind retry safety.

| Class | Effect / retry | Cancellation and unknown result |
| --- | --- | --- |
| `PURE` | No external effect; retry freely. | Stop unfinished computation. |
| `READ_ONLY` | No intended write; retry subject to freshness policy. | Stop/query; return partial only if contract permits. |
| `IDEMPOTENT_WRITE` | Repeat/query with same operation ID where dedupe is supported. | Cancel may leave unknown; query recovery before retry. |
| `NON_IDEMPOTENT_WRITE` | External write may duplicate. | No automatic retry after uncertainty. |
| `PHYSICAL_ACTION` | Changes world/body. | Cancel means stop request, not undo; inspect outcome/human intervention as needed. |

An operation declares class, side-effect flag, idempotency, retryability, deadline behavior, cancellation behavior, and outcome certainty. Terminal outcomes are `SUCCEEDED`, `FAILED`, `CANCELLED`, and `OUTCOME_UNKNOWN`. A timeout is not `FAILED` when execution cannot be proven absent. `OUTCOME_UNKNOWN` is authoritative uncertainty: it blocks blind automatic retries of non-idempotent and physical actions, but may permit same-ID query/dedupe recovery.

`CancelRequest` targets a Turn, stream, or operation; `CancelAck` returns `CANCELLED`, `ALREADY_COMPLETED`, `TOO_LATE`, `NOT_FOUND`, or where necessary `OUTCOME_UNKNOWN`. Duplicate cancellation is idempotent. Completion and cancellation race by authoritative terminal-state ordering: once completion is committed, a later cancel returns `ALREADY_COMPLETED`/`TOO_LATE` and MUST NOT regress state. A `CANCELLED` ack never implies rollback.

## 7. Snapshot, binding, withdrawal, and restart semantics

Admission freezes an immutable `TurnSnapshot`: identity revision/source hash/load time, config revision, model-binding revision, grant revision, and binding revision. Current operations use the binding resolved at their start. A provider withdrawal prevents new operations/bindings; an already accepted operation MAY complete only if its provider and contract can still safely complete it. Otherwise it ends a defined degraded/failed/unknown outcome. An in-progress presentation never silently switches provider; next response may reselect, unless an explicit seamless-handoff contract applies.

Core restart never resumes active turn execution. During recovery, every nonterminal turn becomes `RECOVERY_ABORTED`; draft output is discarded; pending non-idempotent operations are never replayed. Durable committed conversation state may later be read from `CoreStateStorePort`. Runtime generation fencing is provider-side too: stale instance/generation or expired lease is rejected before privileged execution/binding.

## 8. Failure Simulations

| # | Scenario | Deterministic outcome |
| --- | --- | --- |
| 1 | User interrupts Core generation before response commit. | Turn `CANCELLED`; no response/history assistant entry; only committed user input observation MAY exist; stream abort/interrupt receipt records actual extent. |
| 2 | TTS synthesized but playback has not begun, then interrupt. | Response may already be committed/history authoritative; receipt `INTERRUPTED` with zero extent; cache is provider-private and not proof of presentation; Memory gets commit plus zero/interrupt fact. |
| 3 | Voice plays 40%, then interrupt. | Response/history remain committed; `SpeechProgress` and `PARTIALLY_PRESENTED` record 40%; Memory receives those facts; next user turn admits normally without rollback. |
| 4 | WeChat bubble 1 succeeds, bubble 2 fails. | Response remains committed; receipt is partial delivery with message IDs; bubble 2 alone MAY retry only with its own stable operation ID; bubble 1 is not resent. |
| 5 | `memory.write` times out but backend committed. | Provider reports `OUTCOME_UNKNOWN`; same operation ID queries/dedupes recovery; no new durable write request with a new ID. |
| 6 | Tool timeout after side effect. | PURE/READ_ONLY may retry by policy; idempotent write queries/retries same ID; non-idempotent/physical becomes `OUTCOME_UNKNOWN`, no blind retry, escalate when required. |
| 7 | Supervisor crashes. | Existing direct session traffic continues; management operations unavailable; runtimes re-register after restart. |
| 8 | Voice generation 5 reconnects while generation 6 is active. | Generation 5 is `FENCED/STALE_GENERATION`; it cannot gain active binding. |
| 9 | Provider withdraws during an active turn. | Existing accepted operation may finish only if safe; no new operation on withdrawn offer; frozen binding is visible and result is degraded/failed/unknown as appropriate. |
| 10 | Active speaker vanishes; another exists. | Current presentation is interrupted/failed with real extent; no silent switch. The next response may rebind, unless seamless handoff is declared. |
| 11 | Core crashes during active turn. | Turn becomes `RECOVERY_ABORTED`; partial model output is discarded; non-idempotent tools are not replayed; committed state restores later through state port. |
| 12 | SOUL/identity changes mid-long turn. | Current turn uses frozen identity revision/source hash; only a new turn observes the change. |
| 13 | STT revisions 1→2→3→final. | Each higher revision replaces prior text; Core starts a normal turn only after final normalized input commit. |
| 14 | Core semantic stream outruns TTS. | Duration/watermark backpressure and semantic chunking slow producer; bounded buffer; obsolete interrupted output is cancellable; never unlimited queue. |
| 15 | Surface forges owner ID or `wechat.send` grant. | Resolver refuses forged user mapping and/or provider denies missing verifiable authority; capability availability is irrelevant. |
| 16 | Remote stale generation reconnects. | Provider/management plane rejects it as fenced/stale; no active role or privileged operation is restored. |

## 9. Conformance requirements

The R1 test kit MUST test protocol/version compatibility, invalid/malformed requests, deadlines, duplicate IDs, cancellation/completion races, late completion, privacy-bounded errors, unknown additive fields, stale runtime generation, restart recovery, and provider-side authorization. Memory, Voice, Surface, capability-provider, lifecycle, and Core profiles add the cases described in the modular-runtime RFC. Tests MUST prove absence of implementation imports across package boundaries.

## 10. Acceptance criteria and open items

This RFC is accepted when reviewers agree the following are blocker-free: principal, conversation, and turn identities; generated/committed/presented/persisted distinction; response/segment IDs; receipt and partial presentation behavior; idempotency/uncertain outcomes; cancellation races; memory observation boundary; runtime instance/generation/lease fencing; capability offer/selection/binding/authorization and provider enforcement; Core restart; identity/config snapshots; and version taxonomy.

Open implementation choices are transport (gRPC/ConnectRPC, UDS/localhost TCP), credentials, durable Core state backend, distributed config coordination, scheduler/discovery, and full tool framework. They MUST preserve this RFC. No R1 implementation begins before this architecture gate is accepted.
