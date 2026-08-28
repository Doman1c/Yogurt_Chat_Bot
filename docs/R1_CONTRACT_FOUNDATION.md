# Yogurt Modular Runtime R1: Contract Foundation

## Scope and binding boundary

R1 implements normative domain types, a strict Pydantic Python reference binding, state/validation rules, and an isolated conformance kit. It does not implement Yogurt Core, a runtime, transport, durable backend, or a canonical wire ABI. Pydantic names, enums, modules, inheritance, and `model_dump()` are Python-binding details; future transport compatibility is specified separately.

## R1 principles

1. **R1-AP-01 — Normative Domain Semantics** are independent of the Python binding.
2. **R1-AP-02 — Visibility** distinguishes `PUBLIC_CROSS_RUNTIME` and `CORE_DOMAIN_INTERNAL`.
3. **R1-AP-03 — Time** is explicit: cross-runtime timestamps are UTC absolute values; monotonic clocks are process-local.
4. **R1-AP-04 — IDs** have declared generation authorities.
5. **R1-AP-05 — Response identity** is independent of execution attempts.
6. **R1-AP-06 — Operation identity** is independent of dispatch attempts.
7. **R1-AP-07 — Cancellation** is independent of operation outcome.
8. **R1-AP-08 — Facts and projections** are separate models.
9. **R1-AP-09 — Semantic commit** is independent of persistence implementation.
10. **R1-AP-10 — Immutability** is deep semantic immutability: tuples/frozen nested contracts replace mutable containers.
11. **R1-AP-11 — Python strictness** is not future wire compatibility.
12. **R1-AP-12 — Capability extensions** must not require central-framework redesign.

## Compatibility and dependency rules

`ContractVersion(major, minor)` is the only public domain version axis in R1; major is semantic breaking and minor additive compatible evolution. `RuntimeVersion` remains separate. Unknown fields are errors in strict same-version Python construction/config validation. They do not define future transport additive-field behavior.

`yogurt_contracts` is public and imports only stdlib plus Pydantic. `yogurt_core_domain` is internal and may import `yogurt_contracts`; the inverse is forbidden. `yogurt_conformance` is test-only and production packages must not depend on it.

## Durability gate

Semantic commit and `DurabilityLevel` are independent. Real presentation, `wechat.send`, destructive/filesystem writes, robot motion, and durable Memory writes require the configured crash-recovery-safe durability before dispatch. R1 defines requirement/achieved-level contracts and validators only; it implements no database, fsync, or outbox backend.

## Implemented R1 surface

The public Python binding contains typed IDs, time/version/error/idempotency/durability primitives; identity, conversation input/fingerprinting/lifecycle, logical responses and attempts, logical operations and attempts, cancellation/outcome traits, presentation facts/projections, generic capability offers/bindings, runtime identity/fencing/lease, execution snapshots, Memory observations, and stream semantics. `yogurt_core_domain` separately holds internal ledger facts. `yogurt_conformance` contains test-only profiles, fakes, assertions, property tests, Python-binding serialization fixtures, and static identifier negative checks.

The CI job installs `requirements-contracts-dev.txt` and runs isolated contract, conformance, architecture-allowlist, and strict mypy checks. These checks prove Python-binding behavior only; they do not test or promise a future gRPC/JSON/protobuf ABI.

## P0 hardening status

P0-01 durable ledger recovery, P0-02 turn idempotency, P0-03 operation authorization/binding safety, and P0-04 authority-issued runtime fencing are closed. P0-05 Memory scope/exposure is implemented as an external-review candidate: public observations carry persona/user/conversation/turn scope and derive monotonic presentation evidence without treating semantic content commit or delivery as user exposure. P0-06 reusable adapter conformance profiles and good/bad fake barriers are implemented as an external-review candidate. R1 is a final-review candidate; R2 has not started.
