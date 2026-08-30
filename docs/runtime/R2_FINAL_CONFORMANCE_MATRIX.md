# R2 Final Conformance Matrix

This matrix is an evidence index for the frozen R1 through R2-D contracts. It
does not introduce runtime behavior or alter architecture decisions.

| Category | Invariant | Source | Executable evidence | Result | Notes |
| --- | --- | --- | --- | --- | --- |
| R1 Contracts | `ConversationRef` is persona, user, and conversation scoped | R1 identity contract | `test_core_ledger_hardening.py::test_turn_idempotency_uses_only_conversation_ref_and_turn_id` | Green | Different conversation refs do not alias. |
| R1 Contracts | Turn dedupe is TurnId + ConversationRef, never trace correlation | R1 idempotency contract | `test_core_ledger_hardening.py::test_trace_correlation_is_not_part_of_logical_turn_deduplication` | Green | `TurnIdempotencyKey` has exactly the logical fields. |
| R1 Contracts | Same TurnId/fingerprint is same logical turn; mismatch conflicts | R1 idempotency contract | `test_durable_state_store.py::test_sqlite_admission_is_atomic_under_concurrent_retries`; `test_core_ledger_hardening.py::test_turn_idempotency_uses_only_conversation_ref_and_turn_id` | Green | Atomic durable admission. |
| R1 Contracts | Authenticated principal is resolved before a logical user is trusted | R1 identity contract | `test_ephemeral_core.py::test_authenticated_principal_and_response_absence_are_fail_closed` | Green | Arbitrary caller user is rejected. |
| R1 Contracts | Authorization snapshot is immutable and scoped | R1 operation authorization contract | `test_operation_authorization_hardening.py::test_current_authorization_mismatches_are_denied` | Green | Actor/subject/origin/audience/scope and expiry are checked. |
| R2-A Ephemeral Core | Lifecycle graph rejects illegal completion from input committed | R2-A lifecycle | `test_ephemeral_core.py::test_turn_lifecycle_transitions_are_authoritative_and_validate_r1_graph` | Green | Claim remains held on rejection. |
| R2-A Ephemeral Core | A turn has zero or one response; watcher creates no fake response | R2-A response boundary | `test_ephemeral_core.py::test_authenticated_principal_and_response_absence_are_fail_closed` | Green | Missing response is typed `RESPONSE_NOT_FOUND`. |
| R2-A Ephemeral Core | Committed prefixes survive execution abort | R2-A response lifecycle | `test_ephemeral_core.py::test_provider_exceptions_abort_deterministically_and_scheduler_surfaces_unexpected_errors` | Green | Committed segment remains after abort. |
| R2-A Ephemeral Core | Response stream is response-only | R2-A stream boundary | `test_ephemeral_core.py::test_watcher_terminal_cursor_and_live_handoff_do_not_lose_events` | Green | `ResponseEventStream` rejects unrelated values. |
| R2-A/B | Persona is frozen at admission and durable over restart | R2-A snapshot / R2-B durable state | `test_durable_state_store.py::test_persona_snapshot_is_admission_frozen_and_restart_reconstructible` | Green | Restart reconstructs P1 snapshot. |
| R2-B Durable State | Facts are stable, append-only, versioned, and CAS guarded | R2-B ledger | `test_durable_state_store.py::test_stable_fact_id_reconciles_unknown_commit_and_rejects_conflict`; `test_projection_rebuild_upcasts_v0_and_incompatible_fact_stops_serving` | Green | Unknown schema fails closed. |
| R2-B Durable State | Admission, segment, terminal commits expose real certainty | R2-B certainty | `test_durable_state_store.py::test_semantic_commit_unknown_reuses_admission_segment_and_terminal_identities`; `test_semantic_precommit_failure_rolls_back_all_authoritative_paths` | Green | COMMITTED, NOT_COMMITTED, OUTCOME_UNKNOWN. |
| R2-B Durable State | Every projection is facts rebuildable | R2-B projection | `test_durable_state_store.py::test_sqlite_store_persists_atomic_turn_and_terminal_claim_release`; `test_operation_engine.py::test_operation_projection_rebuild_removes_facts_less_bogus_row` | Green | Claims, conversation, turn, response, and operation evidence. |
| R2-C Recovery | Authority ownership is fenced and epochs invalidate stale cores | R2-C authority | `test_sqlite_authority_fences_second_and_stale_core`; `test_operation_identity_fencing_restart_and_projection_rebuild` | Green | Turn, response, and operation writes are fenced. |
| R2-C Recovery | Recovery terminalizes local active work deterministically and is idempotent | R2-C recovery | `test_recovery_closes_input_started_and_converges_terminal_response` | Green | Input-only and active-response turns converge; repeat recovery adds no facts. |
| R2-C Recovery | Bounded stop releases authority after drain failure/timeout | R2-C lifecycle | `test_ephemeral_core.py::test_stop_is_bounded_and_releases_authority_after_timeout_or_drain_failure` | Green | Both timeout and drain exception cancel local work and release authority. |
| R2-D Operations | Intent and dispatch attempt commit before provider work; DB transaction excludes provider | R2-D engine | `test_operation_engine.py::test_operation_engine_durable_ordering_outcomes_and_guards` | Green | Observable fake-provider ordering. |
| R2-D Operations | Operation identity, outcomes, certainty and restart are durable | R2-D engine | `test_operation_engine.py::test_operation_identity_fencing_restart_and_projection_rebuild`; `test_success_outcome_ack_loss_reconciles_durable_terminal_without_redrive`; `test_recovery_keeps_dispatched_operation_pending_without_provider_redrive` | Green | Stable OperationId reconciliation; recovery does not invent provider outcome. |
| R2-D Operations | Capability mismatch and all zero-dispatch guards fail closed | R2-D engine | `test_operation_engine.py::test_operation_engine_rejects_real_provider_capability_mismatch_before_effect`; `test_operation_zero_dispatch_guards_and_ephemeral_boundary` | Green | No provider call/effect. |
| Architecture | Core has no production/heavy provider import | R1/R2 architecture boundary | `test_r2a_dependency_boundaries.py::test_r2a_core_does_not_import_production_or_conformance_packages` | Green | Includes OpenClaw, WeChat, Whisper, torch, GPT-SoVITS, Live2D and robot roots. |
| Architecture | Optional provider absence does not break Core/contract import | R1 optionality | `test_r2e_optional_package_absence.py::test_core_and_contracts_import_with_optional_provider_packages_blocked` | Green | Clean subprocess import isolation; no package is uninstalled. |
| Python compatibility | CI runs runtime/conformance tests on 3.9–3.12 | R2-E final gate | `.github/workflows/ci.yml::test` | Pending GitHub Actions | Local environment supplies one interpreter only. |
| Production regressions | Offline-safe mouth, voice-session, orchestration, runtime, TTS, singing suites | R2-E final gate | Final validation command | Green locally | Forbidden live/Native Voice/OpenClaw smoke is excluded. |

## Failure simulation traceability

The frozen RFC failure simulations are covered by the listed invariant tests:
duplicate/conflicting turn and operation identities by the idempotency and
operation tests; stale ownership and restart by durable authority/recovery
tests; provider exception, malformed result, capability rejection, and
uncertain outcome by `test_operation_engine.py`; malformed/corrupt ledger and
schema incompatibility by `test_durable_state_store.py`; and response ordering,
cancellation races, committed-prefix preservation, and watcher boundaries by
`test_ephemeral_core.py` and `test_core_ledger_hardening.py`.
