# Yogurt Runtime Contracts & Execution Semantics RFC v0.1

- Status: Architecture gate proposal for acceptance
- Scope: R0.5 semantic contract only; no protobuf, transport, backend, or runtime implementation
- Depends on: [Yogurt Modular Runtime RFC v0.2](YOGURT_MODULAR_RUNTIME_RFC_V0_2.md)
- Normative language: **MUST**, **MUST NOT**, **SHOULD**, and **MAY** are normative.

## 1. Definitions, authoritative writers, and identity

| Concept | Definition and authoritative writer | Commit, privacy, and version rule |
| --- | --- | --- |
| `ExternalPrincipal` | Authenticated external-subject evidence. A trusted Surface runtime writes it. | Opaque and provider-scoped; raw credentials never cross a domain boundary. |
| `IdentityResolution` / `LogicalUser` | Resolver maps verified principal to domain user. Identity Resolver is sole writer. | Revisioned; a Surface cannot self-assert `user_id=owner`. |
| `ConversationRef` | `{persona_id, user_id, conversation_id}` conversation namespace. Core writes ledger facts. | `conversation_id` is opaque, bounded, privacy-safe, and has no surface/routing/permission encoding. |
| `TurnRef` / `TurnOrigin` | A logical interaction and its source `{origin_surface, external_principal_ref, identity_resolution_ref}`. Core writes it at admission. | Origin supports policy, authorization, and audit; it does not alter conversation/persona identity. |
| `Response` / `Segment` | A response assembly and ordered semantic parts. Core writes content facts. | Stable `response_id` is reserved at response start; each committed segment has `segment_id`. |
| `ConversationLedger` | Append-only conversation-domain facts. Core writes Core facts; presentation layer is authoritative for its receipt, which Core records/links. | Facts are immutable, correlated, versioned, and privacy-bounded. |
| `PresentationOperation` / `Receipt` | A rendering/delivery request and observed result. Presenting provider writes receipt. | Both have stable IDs; receipt never mutates content or commits. |
| `Operation` | Any side-effect request, keyed by `operation_id`. Executing provider writes outcome. | One ID means one logical request/fingerprint; universal exactly-once is not promised. |
| `ContentObservation` / `PresentationObservation` | Immutable fact sent to Memory Runtime. Core emits them; Memory writes memory policy/state. | Independent operations; no raw drafts/provider output. |

Each cross-runtime privileged request carries protocol/schema version, `CallerIdentity`, delegated authority where required, deadline, correlation trace, and its domain IDs. `trace_id` is correlation only, never an idempotency, permission, conversation, or fencing key. Unknown security-critical values MUST fail closed.

Authorization claims are distinct: **Actor** is the runtime/service invoking a provider; **Subject** is the `LogicalUser` represented; **Origin** is the initial authenticated Surface/principal context; **Audience** is the provider/runtime receiving delegated authority; **Scope** is the capability/resource; **Expiry** limits validity; and **GrantRevision** identifies the frozen policy snapshot. Example: Actor=`yogurt.core` instance X; Subject=`user.owner`; Origin=`native_voice:mac`; Audience=Memory Runtime instance Y; Scope=`memory.read`. Providers MUST validate Actor, Audience, Scope, Expiry, and delegation rather than trusting a Core assertion. Identity Resolver accepts `ExternalPrincipal` evidence only from a trusted, authenticated Surface runtime; a Surface cannot manufacture trusted evidence. Credential transport remains open.

## 2. Conversation Ledger, admission, and lifecycle

`ConversationLedger` is not `[user string, assistant string]`. It is an append-only sequence of immutable facts at minimum: `InputCommitted`, `ResponseStarted`, `SegmentCommitted`, `ResponseFinalized`, `ResponseAborted`, `PresentationReceipt`, `TurnCompleted`, and `TurnAborted`. Core is the authoritative writer for Core facts and records correlated presentation facts; Surface/Voice is authoritative for what it observed. Facts retain `ConversationRef`, `TurnRef`, response/segment IDs when applicable, snapshot references, time, provenance, and schema version.

Turn admission is:

```text
RECEIVED → INPUT_COMMITTED → PROCESSING → [response lifecycle] → TURN_COMPLETED
                 │                 │
                 └── REJECTED      └── CANCELLED | ABORTED | RECOVERY_ABORTED
```

`RECEIVED` is validated but is not conversation state. Core writes `InputCommitted` only after trusted identity resolution, isolation, validation, dedupe/admission, origin capture, and snapshot compatibility checks. Final STT is normalized and admitted before this transition; partial STT revisions are replacements, not turn inputs. `REJECTED` is exclusively an admission/validation/authentication failure before `INPUT_COMMITTED`. `CANCELLED` is an accepted cancellation before irreversible terminal closure. `ABORTED` is an execution failure after `INPUT_COMMITTED` but before normal response closure. `RECOVERY_ABORTED` is exclusively a nonterminal turn found after Core restart. `FAILED` is not a Turn terminal state; it is an operation or presentation outcome. These meanings do not overlap, and no terminal fact regresses.

The response lifecycle is independent and segment-granular:

```text
ResponseStarted(response_id)
  → SegmentCommitted #1 → presentation of #1 MAY start
  → SegmentCommitted #2 → presentation of #2 MAY start
  → ...
  → ResponseFinalized
  or ResponseAborted(reason: CANCELLED | ABORTED | RECOVERY_ABORTED)
```

`ResponseStarted` reserves `response_id`; it does not commit content. Every `SegmentCommitted` atomically appends an immutable normalized segment to the ledger. `ResponseFinalized` closes the assembly and declares no more segment commits are permitted. `ResponseAborted` closes further generation without deleting committed segments. A response may therefore be an authoritative incomplete assembly with an abort reason; it MUST NOT be labeled a complete success, nor as if it never happened.

## 3. Generated, committed, presented, persisted, and projection

| Fact | Meaning | Writer / transition |
| --- | --- | --- |
| Generated | Provider produced candidate material. | Provider draft only. |
| Segment committed | Core normalized and appended a particular immutable semantic segment. | Core `SegmentCommitted`. |
| Response finalized | Core closed the ordered response assembly. | Core `ResponseFinalized`. |
| Presented | A presentation provider observed a specific segment/range extent rendered or delivered. | Surface/Voice `PresentationReceipt`; may be partial. |
| Persisted | A domain writer durably stored its own fact/state. | Core, Memory, or provider independently; presentation does not imply it. |

No draft semantic content MAY leave Core for actual user presentation. TTS, Surface, Voice, and Embodiment presentation consumers MUST accept only `AgentOutputSegmentCommitted`; a committed segment may start realtime presentation before later segments or finalization exist. Send acceptance does not mean delivery; delivery does not mean a WeChat user read content. Presentation never rolls back a committed segment; correction is a later segment/response fact.

`ContextProjection` is ContextComposer's derived effective conversational view, not a direct dump of the ledger nor all committed response text. It MUST use committed semantic content, presentation extent, `TurnOrigin`, and surface semantics. For response text `A B C D E` where Voice actually presented only `A B`, the next projection MUST NOT silently assume exposure to `C D E`; it records committed-but-unexposed remainder and applies surface/policy-specific handling. Prompt formatting remains open. Projection is a derived view, never an authoritative writer and never a mutation of ledger facts.

## 4. Semantic output, presentation, and tool lanes

Raw model tokens/deltas, provider JSON, reasoning, tool grammar, and structured fragments MUST NOT cross the Core domain boundary. The **Agent Semantic Output Stream** is Core→Surface/Voice/Embodiment-facing and contains only:

```text
AgentResponseStarted(response_id, TurnRef)
AgentOutputSegmentCommitted(response_id, segment_id, sequence, text, language,
                            affect, interruptibility, content_hash?, is_segment_final)
AgentResponseFinalized(response_id, assembly_hash)
AgentResponseAborted(response_id, TurnRef, reason)
```

Segments are immutable and ordered by `sequence`; repeated committed delivery is deduplicated by response/segment identity and content hash where present. Core MUST NOT publish an `AgentOutputSegment` that is not committed, and presentation consumers MUST reject it. Semantic stream backpressure is bounded by duration and high/low watermarks; Core uses semantic chunking and MUST NOT build an unlimited queue.

The **Tool Control Lane** is a separate Core↔Tool Runtime privileged lane. It carries `ToolCallRequested(operation_id, authority, deadline, side_effect_classification, normalized_request)` and `ToolCallCompleted(operation_id, outcome)`. Surface, Voice, and Embodiment consumers MUST NOT receive tool payload by default.

`PresentationOperation` is a side-effect request:

```text
{ operation_id, response_id, segment_id_or_range, presentation_target,
  authority, deadline, idempotency_policy }
```

Each WeChat bubble is either a child operation with its own stable `operation_id` or a declared equivalent parent/child scheme. `PresentationReceipt` MUST contain `{receipt_id, operation_id, response_id, segment_id_or_range, presentation_target, status, presented_extent, observed_at, external_reference?}`. `receipt_id` deduplicates receipt events. `ACCEPTED` means provider accepted work; `DELIVERED` means provider/platform confirmed delivery; `PRESENTED` means the provider can prove rendering; `PARTIALLY_PRESENTED` records actual extent; `INTERRUPTED` records stopped work; `FAILED` records presentation outcome. Out-of-order/duplicate receipts are retained/deduplicated as facts and cannot regress the greatest known extent or change a committed segment.

## 5. Memory append-only observation boundary

Core does not extract or score long-term memory. On a segment or final response commit it MAY send an immutable `CommittedContentObservation` containing conversation, turn, response, optional segment, speaker, committed content, provenance, timestamp, and a **content-observation operation ID**. Later receipt facts are sent as distinct immutable `PresentationObservation` operations containing the same correlation IDs, receipt/extent, provenance, and a distinct **presentation-observation operation ID**. Neither event mutates or reuses the other's operation ID.

Memory Runtime correlates observations by `conversation_id`, `turn_id`, `response_id`, and `segment_id`; it remains the authoritative writer for candidate extraction, filtering, dedupe, scoring, retrieval, index, lifecycle, and persistence. A Core timeout after sending content observation is `OUTCOME_UNKNOWN` until same-ID query/deduplication recovers the result. A later presentation observation is a new logical operation regardless of content-operation recovery. Raw drafts, cancelled uncommitted drafts, uncommitted tool result, provider reasoning, and raw audio MUST NOT become durable memory.

## 6. Side effects, outcomes, retries, and cancellation

All side effects require an `operation_id` distinct from turn/response/segment/trace IDs. Equal operation ID means equal request fingerprint; conflicting reuse is `FAILED/IDEMPOTENCY_CONFLICT`. Providers SHOULD support durable dedupe/query recovery where possible and MAY use native idempotency. Yogurt does not guarantee universal exactly-once.

| Class | Retry and cancellation rule |
| --- | --- |
| `PURE` | No external effect; stop unfinished work or retry freely. |
| `READ_ONLY` | Retry subject to freshness policy; stop/query as contract permits. |
| `IDEMPOTENT_WRITE` | Query/retry with same operation ID where dedupe exists; cancellation may remain uncertain. |
| `NON_IDEMPOTENT_WRITE` | Never blind-retry after uncertainty. |
| `PHYSICAL_ACTION` | Cancel means request stop, never undo; inspect outcome or require human intervention. |

Operations declare class, side-effect flag, idempotency, retryability, deadline, cancellation, and outcome-certainty semantics. Outcomes are `SUCCEEDED`, `FAILED`, `CANCELLED`, and `OUTCOME_UNKNOWN`; timeout is not `FAILED` when execution cannot be proven absent. `CancelRequest` targets a Turn, response stream, or operation. `CancelAck` is `CANCELLED`, `ALREADY_COMPLETED`, `TOO_LATE`, `NOT_FOUND`, or `OUTCOME_UNKNOWN`. Duplicate cancellation is idempotent. Once an authoritative terminal fact is committed, late cancel cannot regress it. Cancellation never rolls back committed segment, presentation, memory, or physical facts.

## 7. Execution snapshot, runtime slots, binding, lease, and restart

Admission freezes an immutable `ExecutionSnapshot`, not a misleading single global revision. It contains Core config revision; identity revision/source hash/load time; grant revision; model binding ID/revision/provider instance/generation and provider-applied config revision where relevant; presentation/voice binding ID/revision/provider instance/generation, validity, and provider-applied config revision where relevant; and `TurnOrigin`. Mixed runtime revisions MAY exist. Core admits a Turn only when every selected required binding is compatibility-valid against Core's contract/config snapshot; compatible Core N and Voice N-1 is allowed and recorded, while protocol/config incompatibility rejects admission or marks the required capability degraded.

Runtime identity is `{runtime_type, runtime_slot_id, runtime_instance_id, runtime_generation, node_id}`. `runtime_slot_id` is stable logical lineage such as `voice.mac-main` or `voice.stackchan`; generation is monotonic and comparable/fencing-valid only within that slot. The durable generation authority in management-plane state allocates it and MUST survive Supervisor restart. Thus `voice.mac-main` generation 6 fences its generation 5, but does not fence `voice.stackchan` generation 2.

`RegistrationLease` proves registration/discovery liveness. A `CapabilityBinding` separately records binding ID/revision, provider instance/generation, endpoint, authority validity, and lease reference. Supervisor outage does not immediately kill a valid active binding: existing operations MAY continue only inside the binding's existing authority/validity contract. During outage, new discovery, new binding, registration-lease renewal, and management mutation are unavailable. If binding authority expires, provider MUST stop accepting new privileged operations; no permanent-outage traffic guarantee exists. Withdrawal cannot retroactively change which provider started an operation; it blocks new operations under withdrawn/expired bindings.

Core restart never resumes active execution. Nonterminal turns emit `TurnAborted(RECOVERY_ABORTED)` and active responses emit `ResponseAborted(RECOVERY_ABORTED)`; committed segments and real receipts remain ledger facts, while uncommitted draft disappears. Pending non-idempotent tools are never replayed. Future `CoreStateStorePort` may restore durable ledger facts only.

## 8. Failure simulations

| # | Scenario | Deterministic outcome |
| --- | --- | --- |
| 1 | User interrupts generation before a segment commit. | Turn is `CANCELLED`; no assistant segment/history fact; committed input observation MAY remain. |
| 2 | TTS synthesized but zero playback, then interrupt. | Already committed segment remains in ledger; receipt is `INTERRUPTED` with zero extent; cache proves neither delivery nor presentation. |
| 3 | Segment 1 committed/presented; segment 2 committed but Voice reaches 40%, then interrupt. | Ledger retains both segment commits and 40% receipt; response is `ResponseAborted(CANCELLED)` unless finalized; projection exposes segment 1 plus only presented extent of segment 2; Memory receives independent content/presentation observations. |
| 4 | WeChat bubble 1 delivered; bubble 2 fails. | Both are committed content segments; bubble 1 receipt stays delivered; only bubble 2's stable child operation may recover/retry, never resend bubble 1. |
| 5 | `memory.write` timeout but backend committed. | `OUTCOME_UNKNOWN`; same content-observation operation ID queries/dedupes; no duplicate durable write. |
| 6 | Tool times out after side effect. | PURE/READ_ONLY follow policy; idempotent write uses same-ID query/retry; non-idempotent/physical is unknown without blind retry and may need intervention. |
| 7 | Supervisor crashes. | Valid existing bindings/operations MAY continue only for authority lifetime; management work is unavailable; runtimes re-register after recovery. |
| 8 | Voice generation 5 reconnects while same-slot generation 6 is active. | Generation 5 is `FENCED/STALE_GENERATION`; no active binding. |
| 9 | Provider withdraws during a turn. | Accepted operation stays attributed to frozen provider and may finish only if safe; new operations are forbidden; otherwise degraded/failed/unknown. |
| 10 | Active speaker vanishes while another is available. | Current presentation ends with real receipt; no silent switch; next response may rebind unless seamless handoff is declared. |
| 11 | Core crashes during active turn. | Response/turn become recovery-aborted; draft is discarded; committed segments and receipts remain; non-idempotent tools do not replay. |
| 12 | Identity changes mid-turn. | Current execution uses frozen identity revision; new turn uses new revision. |
| 13 | STT revisions 1→2→3→final. | Revisions replace prior hypothesis; only final normalized input can commit a normal turn. |
| 14 | Semantic producer outruns TTS. | Bounded backpressure/chunking applies; no unlimited queue; only committed segments may be presented. |
| 15 | Surface forges owner/grant. | Trusted resolver and/or provider-side Actor/Audience/Scope validation rejects it. |
| 16 | Remote stale instance reconnects. | Same-slot stale generation is fenced; it restores neither active role nor privileged execution. |
| 17 | Segment 1 committed and spoken; segment 2 draft; Core crashes before finalization. | Segment 1 and receipt remain authoritative; segment 2 disappears; response/turn recovery-abort; projection has no ghost speech. |
| 18 | Supervisor outage outlasts RegistrationLease. | Discovery/renewal unavailable; valid binding continues only to its own authority validity; expired authority blocks new privileged operations. |
| 19 | `voice.mac-main@6` and `voice.stackchan@2` coexist. | Both are legitimate slots; generation 6 does not fence generation 2. |
| 20 | Receipt is duplicated or arrives out of order. | `receipt_id` deduplicates; stale receipt cannot reduce known presented extent. |
| 21 | Core N and Voice N-1 are protocol/config compatible. | Admission succeeds and records exact applied revisions in `ExecutionSnapshot`. |
| 22 | Core N and Voice N-1 are protocol/config incompatible. | Core rejects admission or marks required capability degraded; no misleading turn starts. |

## 9. Conformance requirements

R1's future Contract Conformance Test Kit MUST cover protocol compatibility, malformed request, deadline, duplicate ID, cancellation race, late completion, privacy, additive fields, stale runtime, restart, and authorization, plus: segment commit before presentation; rejection of draft presentation; partial-response projection; duplicate/out-of-order receipt; content observation followed by distinct presentation observation; slot-scoped durable generation; Supervisor outage/lease expiry; Actor/Subject/Origin/Audience/Scope checks; PresentationOperation idempotency; tool-payload isolation; and compatible/incompatible mixed-revision admission. Tests MUST prove package import boundaries.

## 10. OPEN QUESTIONS and Architecture Gate

Open implementation choices are gRPC vs ConnectRPC, UDS vs localhost TCP, concrete credentials, CoreStateStore backend, distributed configuration orchestration, scheduler/discovery, AEC, semantic turn detection, Vision model, and full tool framework. These choices MUST preserve this RFC.

## 11. BLOCKERS BEFORE R1

**NONE — pending external architecture review acceptance.** This revision resolves segment-level commits, projection after partial presentation, append-only Memory/presentation observations, runtime generation scope/durability, Supervisor outage/lease semantics, Actor/Subject authorization, presentation-operation idempotency, tool-lane separation, and execution-snapshot compatibility. No R1 implementation is authorized before external acceptance.
