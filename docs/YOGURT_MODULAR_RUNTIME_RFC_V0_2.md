# Yogurt Modular Runtime RFC v0.2

- Status: Accepted architecture baseline
- Scope: R0 Architecture Freeze
- Supersedes: no file; any future v0.1 document remains preserved
- Normative language: **MUST**, **MUST NOT**, **SHOULD**, and **MAY** are normative.

## 1. Decision and scope

Yogurt is an identity, agent runtime, state, and capabilities. It is not Qwen, OpenClaw, or GPT-SoVITS. This RFC freezes the long-lived modular boundary before business-code implementation. It does not migrate, refactor, or alter OpenClaw/WeChat, Memory V5, VoiceSession/Mouth, Voice Runtime, TTS, Singing, Native Voice prototype, or SOUL/IDENTITY/USER content.

The companion [execution-semantics RFC](YOGURT_RUNTIME_CONTRACTS_EXECUTION_SEMANTICS_RFC_V0_1.md) defines wire-independent contracts and failure behavior. R1 MAY choose transport and storage implementations only if they preserve both RFCs.

## 2. Architecture principles

Existing principles remain in force: hybrid modular runtime; Core/Runtime/Surface separation; ports and adapters; provider-neutral Model/STT/TTS ports; isolated heavy/failure-domain runtimes; package/import modularity; bounded cancellable streams; stage-testable pipelines; capability-based design; typed and versioned contracts; and no global unfiltered event bus.

| ID | Principle |
| --- | --- |
| AP-14 | Generated output is not committed output. |
| AP-15 | Every side effect has an idempotency boundary. |
| AP-16 | Cancellation is best-effort and never implies rollback. |
| AP-17 | Runtime identity is instance-scoped and fenced by generation. |
| AP-18 | One domain concept has one authoritative writer. Caches, replicas, snapshots, and materialized views are permitted but are not writers. |
| AP-19 | Availability, authorization, selection, and binding are different concepts. |
| AP-20 | Raw provider output MUST be semantically normalized before it crosses a Yogurt domain boundary. |
| AP-21 | User-visible state MUST be acknowledged by the presentation layer that can actually observe it. Send success MUST NOT mean user read. |
| AP-22 | Optional capability removal MUST hold at package/import level, not merely behind flags. |
| AP-23 | Trace identity MUST NOT substitute for domain identity. |
| AP-24 | No semantic draft may reach a user; presentation consumes immutable committed segments only. |
| AP-25 | Real presentation requires a crash-durable Core semantic-ledger commit barrier. |

## 3. Target topology and traffic lanes

```text
                  Yogurt Supervisor — management plane only
 registration / discovery / leases / config / grants / offers / bindings
                              │
Surface ── session control ──► Yogurt Core ◄── VisionObservation ─ Vision Runtime
  ▲                              Agent authority │
  │ PresentationReceipt                            ├── Memory Runtime
  │                                                └── ModelPort → Ollama adapter → Ollama/Qwen
  │
Voice Runtime ◄──── session control / semantic stream ───► Core
  │ media session, VAD, STT, TTS, playback, SpeechProgress
  │
Core semantic intent ──┐
Voice timing ──────────┼──► Embodiment Runtime ─► Live2D / VRM / StackChan / future body
                       └── body binding, expression mapping, timing, multi-body fanout
```

The Supervisor owns management-plane facts only: registration, discovery, leases, health, configuration, grants, compatibility, offers, bindings, and lifecycle. It MUST NOT carry normal turns, response data, TTS/STT/media data, memory data, or presentation traffic. A Supervisor outage makes new discovery, binding, lease renewal, and management mutation unavailable, but does not immediately kill an active valid binding. Existing traffic MAY continue only inside the binding's own authority lifetime; expired authority blocks new privileged operations.

The high-priority, low-bandwidth session-control lane is direct Surface↔Core, Voice↔Core, or Core↔Runtime and carries turn lifecycle, response commit, cancellation/interrupt, presentation receipts, scoped errors, and conversation end. Data streams carry only durably committed `AgentOutputSegmentCommitted`, `TranscriptHypothesis`, `TTSAudioChunk`, `SpeechProgress`, and `VisionObservation`; each is bounded, backpressured, cancellable, and stream-scoped. A universal `type/payload` frame and raw global event bus are prohibited. Raw audio remains Voice-internal; raw camera frames remain Vision-internal by default.

## 4. Ownership and package boundaries

`yogurt-contracts` is the shared package. `yogurt-core`, memory/voice/vision/embodiment runtimes, and Surface adapters depend on it; they MUST NOT depend on one another's implementations except through contracts.

`yogurt-core` is the authoritative writer for conversation, turn, normalized response, response commit, and emitted observation facts. Memory Runtime is the authoritative writer for memory candidates, filtering, dedupe, scoring, retrieval, index, lifecycle, and persistence. Identity Resolver is the authoritative writer for external-principal-to-logical-user mappings. A Surface owns its external authentication and external-thread/message mapping. Voice owns media sessions, VAD/STT/TTS/playback/progress. Embodiment owns body binding/timing/expression synchronization. Supervisor owns management facts.

Core MUST NOT import OpenClaw, a WeChat SDK, Whisper, GPT-SoVITS, `torch` due to a Vision/TTS dependency, a specific memory backend, or Live2D. R1 MUST add dependency-boundary and architecture-import tests. Existing systems are reusable implementation assets behind later adapters, not dependencies of R0.

## 5. Identity, conversation, and snapshots

An authenticated Surface supplies an `ExternalPrincipal`, never a self-asserted owner/user ID. The chain is:

```text
ExternalPrincipal → Surface Authentication → Identity Resolver → LogicalUser
```

`CallerIdentity` identifies a calling runtime or delegated actor; `LogicalUser` is the domain user; `PersonaRef` identifies the identity/persona in effect; `IdentityResolution` records resolver revision, source hash, and provenance. Native Voice v1 MAY authenticate a trusted local-device principal such as `local-device:<opaque-device-id>` and resolve it to the owner-equivalent user. It MUST NOT claim speaker verification.

`ConversationRef = {persona_id, user_id, conversation_id}`. `conversation_id` MUST be opaque, bounded, and privacy-safe; it MUST NOT encode surface, permissions, routing, or agent prefixes. `TurnRef = {conversation_id, turn_id, parent_turn_id?, sequence?}` identifies one logical interaction. Each admitted turn separately stores `TurnOrigin = {origin_surface, external_principal_ref, identity_resolution_ref}` for policy, authorization, audit, and surface behavior. `TurnOrigin` MUST NOT pollute persona or conversation identity. `media_session_id`, `external_thread_id`, `external_message_id`, `connection_id`, `response_id`, `segment_id`, `operation_id`, `runtime_instance_id`, and `trace_id` have distinct lifetimes and MUST NOT be repurposed.

At turn admission, Core binds an immutable `ExecutionSnapshot`: Core config revision, identity snapshot, grant snapshot, `TurnOrigin`, and a modular `capability_binding_snapshots[]` collection. Every binding snapshot identifies capability, binding/revision, provider slot/instance/generation, optional provider-applied config revision, contract/capability version, and authority validity. Model, presentation, Voice, Memory, and future Vision are collection entries rather than top-level schema fields. It MUST NOT pretend a single `config_revision` proves all distributed providers applied the same configuration. A reload changes later turns only. Trace IDs are observability-only and MUST NOT be used for idempotency, permission, conversation identity, or fencing.

## 6. Runtime and capability model

Runtime identity is `{runtime_type, runtime_slot_id, runtime_instance_id, runtime_generation, node_id}`. `runtime_slot_id` is a stable logical lineage, e.g. `voice.mac-main` or `voice.stackchan`; a generation is monotonic and fencing-valid only within that slot. The management plane's durable generation authority allocates generations and MUST survive Supervisor restart. A registration lease contains `lease_id`, expiry, slot, instance ID, and generation. If `voice.mac-main` generation 6 is active, its generation 5 is `STALE_GENERATION/FENCED` and MUST NOT regain binding; it does not fence `voice.stackchan` generation 2.

Process state (`STARTING`, `RUNNING`, `STOPPING`, `EXITED`), service state (`NOT_READY`, `SERVING`, `DEGRADED`), and capability state (`AVAILABLE`, `DEGRADED`, `UNAVAILABLE`, `INCOMPATIBLE`) are independent. A RUNNING Voice process may have `listen` AVAILABLE, `speak` UNAVAILABLE, and `interrupt` AVAILABLE.

Every capability has distinct concepts:

- **Descriptor**: typed static features, e.g. TTS streaming/emotion/intensity/viseme/languages/sample rates; not an unbounded metadata bag.
- **Offer**: capability ID, contract version, provider instance/generation, node, scope, descriptor, dynamic state, lease, and endpoint.
- **Selection**: a policy decision that an eligible offer fits a request.
- **CapabilityBinding**: a durable/scoped choice `{binding_id, binding_revision, scope/conversation?, capability_id, provider_instance_id, provider_generation, endpoint, authority_validity, lease_ref}`.
- **Authorization**: a grant permits a caller/delegation to use a capability/resource scope.

Availability does not grant access; authorization does not choose a provider; selection does not create a binding; a binding does not prove current availability. Authorization explicitly distinguishes Actor (calling runtime), Subject (represented logical user), Origin (authenticated interaction context), Audience (delegated provider/runtime), Scope (capability/resource), Expiry, and GrantRevision. Sensitive providers MUST revalidate these claims and `DelegatedAuthority` at their boundary. Caller-supplied grants are invalid. This RFC does not select mTLS, JWT, macaroon, local token, or UDS credentials, but a verifiable caller and non-self-asserted authority are mandatory.

Bindings used by a presenting response are frozen to the turn snapshot. Replacement is permitted on a subsequent turn. Mid-response switching is prohibited unless the capability contract explicitly declares seamless handoff; otherwise the current presentation ends failed/interrupted with its real extent.

`RuntimeManifest` distinguishes `startup_dependencies`, `functional_dependencies`, and `optional_bindings`. Most runtimes SHOULD start independently. Voice may run with microphone/STT/TTS available while `native_voice.conversation` is unavailable because no Core binding exists.

## 7. Providers, streams, and semantic normalization

Core talks to a `ModelPort` and an `OllamaModelAdapter` initially. A forwarding-only Model Gateway MUST NOT be introduced. A future gateway requires a real need such as multi-model routing, fallback, remote GPU, load balancing, or rate limiting. Replacing a model MUST NOT alter domain logic; provider/prompt adapters and capability negotiation absorb model differences. Persona regression tests are required later because identity independence does not ensure behavior equivalence.

Provider `ModelToken`, `ModelDelta`, tool grammar, reasoning, provider JSON, and structured fragments are internal. Core writes an append-only ConversationLedger and exposes `AgentResponseStarted`, immutable `AgentOutputSegmentCommitted`, `AgentResponseFinalized`, and `AgentResponseAborted` on the presentation-facing semantic stream. A semantic segment MUST commit before presentation; response finalization closes the assembly but does not delay realtime presentation of earlier committed segments. Tool calls use a separate privileged Core↔Tool Runtime control lane and are not public Surface/Voice semantic output. Semantic output does not contain chain-of-thought, raw provider protocol, or tool payload.

STT hypotheses are revisioned replacements, not append-only text: `{hypothesis_id, revision, text, stability?, is_final}`. Higher revision supersedes the prior hypothesis. Core begins a normal conversational turn only on final input commit, unless a later explicit speculative mode changes this contract.

Each stream declares its own policy: camera is latest-wins; STT partials supersede; input audio is normally not arbitrarily dropped; interrupted speech is cancellable; semantic output uses producer backpressure and semantic chunking. Contracts use duration and high/low watermarks, not queue length alone, and MUST prohibit unlimited response buffers.

## 8. Configuration, versions, privacy, and recovery

Configuration remains split into `core.toml`, `model.toml`, `memory.toml`, `voice.toml`, `vision.toml`, `surfaces.toml`, and `supervisor.toml`. A `ConfigSnapshot` has revision, hash, and creation time. Apply is: load all → validate all → prepare plan → compatibility/readiness checks → commit active revision → runtimes apply → report applied revision. `prepared`, `active`, `applied`, and `failed apply` are distinct. This is not a distributed transaction: partial application MUST be visible, and a runtime MUST report its applied revision rather than imply Core=N, Voice=N-1, Model=N+1 is coherent. Mixed revisions MAY exist, but admission MUST verify every selected required binding is contract/config compatible with the Core snapshot; incompatible turns are rejected or capability-degraded. Fields are dynamic, restart-required, immutable, or secret. Rollback/abort refers to the control-plane revision and does not undo side effects already performed under a valid prior revision.

`runtime_version`, `protocol_version`, `schema_version`, and `capability_version` are separate. Example: Memory runtime 2.8.1, wire protocol 5.2, capability `memory.read@5`. Future protobuf follows: never reuse numbers; reserve removed fields; enum zero is `UNSPECIFIED`; additive compatible changes stay major; semantic breaking changes bump major; unknown security-critical enums fail closed.

Logs/metrics/traces MUST NOT contain raw user content, raw memory, raw audio, private prompts, or chain-of-thought by default. Errors are privacy-bounded. A Core restart does not resume active streams: all active turns become `RECOVERY_ABORTED`; old token streams and pending non-idempotent tools are never replayed. Future `CoreStateStorePort` may restore durable committed conversation state only. A production-capable durable Core state store is a gate before Native Voice or any other real side-effecting presentation capability binds; an `EphemeralLedger` is development/test-only and MUST NOT bind such a capability.

## 9. R1 conformance and roadmap

R1 supplies contracts plus a Contract Conformance Test Kit, not merely Python interfaces. Profiles are Core, Memory, Voice, Surface, Capability Provider, and Runtime Lifecycle. Tests cover durable commit before real presentation; ephemeral-Core capability denial; Turn same-ID retry and fingerprint conflict; input observation and assistant exposure gating; generic binding snapshots; partial-response projection; duplicate/out-of-order receipts with independent extent/outcome; distinct content/presentation observations; slot-scoped durable generation; Supervisor outage/lease expiry; bounded revocation/authority expiry; Actor/Subject/Origin/Audience/Scope authorization; presentation-operation idempotency; tool payload isolation; and compatible/incompatible mixed-revision admission, in addition to compatibility, cancellation, idempotency, privacy, restart, malformed input, and import-boundary tests.

Roadmap: R0 architecture freeze; R0.5 contracts gate; R1 contract foundation/test kit; R2 Core foundation with fakes and state-store port; R3 Memory V5 adapter; R4 Native Voice PTT direct to Core **only after a production-capable durable CoreStateStore gate**; R5 streaming voice; R6 WeChat Core shadow (also behind the durable-store gate for real presentation); R7 WeChat bridge with OpenClaw narrowed to a Surface adapter; R8 Vision; R9 Embodiment; R10 distributed nodes.

## 10. ACCEPTED DECISIONS

- Supervisor is management-only and not a normal-turn hop.
- Generated, durably segment-committed, response-finalized, presented, and persisted facts are separate; no draft or non-durable content can be presented.
- Core is agent authority; Memory/Voice/Embodiment/Surfaces own their stated domains.
- Capability descriptor/offer/state/selection/binding/authorization and runtime slot/instance/generation/lease are separately modeled.
- Embodiment is a dedicated runtime; body animation is a presentation side effect and cannot roll back a committed response.
- Existing runtimes are not being migrated in this phase.

## 11. REQUESTED CHANGES RESOLVED

This revision changes “one owner” to one **authoritative writer**, introduces principal resolution, separated IDs and snapshots, explicit runtime fencing, provider-side authorization, bounded semantic streams, STT replacement, configuration-apply visibility, a dedicated Embodiment Runtime, and package-level optionality. The second gate adds a ConversationLedger, segment-before-presentation commit, ContextProjection, append-only Memory/presentation observations, scoped durable generations, outage/lease separation, explicit authorization claims, PresentationOperation IDs, tool-lane isolation, and ExecutionSnapshot compatibility. The final gate adds durable semantic commits, Turn idempotency, input observations, exposure-aware assistant Memory policy, generic capability snapshots, orthogonal presentation state, and bounded authority revocation. It corrects “model changes are config-only” to domain-logic independence with adapter/capability and regression-test work.

## 12. OPEN QUESTIONS

gRPC vs ConnectRPC; Unix socket vs localhost TCP; mTLS/JWT/token implementation; CoreStateStore backend; distributed configuration transaction mechanics; AEC; semantic turn detector; Vision model; full tool framework; remote discovery; scheduler implementation.

## 13. DEFERRED IMPLEMENTATION

No contracts package, protobuf, gRPC, Core, Supervisor, adapter, registry, tool framework, or migration is implemented here. Concrete storage, transport authentication, seamless handoff, remote-node discovery, and configuration orchestration are deferred to later RFCs/implementation while preserving these semantics.

## 14. BLOCKERS BEFORE R1

**NONE — pending external architecture review acceptance.** R1 MUST NOT start until external review accepts both RFCs; this status does not authorize implementation.
