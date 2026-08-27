# Memory V5-B.7 Controlled Recall Gate Shadow Experiment

## Why B.7 exists

V5-B.6 measured the fixed synthetic corpus using the real V5 service baseline
of 0.52. Its 0.36 counterfactual preserved zero false recall and all three
safety proxies while increasing correct positive Gate recall from 13/83 (15.66%)
to 21/83 (25.30%). B.7 validates the decision delta on real traffic without
changing live Recall behavior.

**0.36 is not the production default in V5-B.7.** The production Gate remains
at 0.52. **Real Recall remains disabled.** No memory is injected into prompts.

## Architecture

```text
real candidate + real state
        ├─ production Gate (0.52) → existing recall response
        └─ B.7 pure evaluator (0.36) → bounded experiment audit only
```

The evaluator runs after the one real Gate evaluation. It never creates a
second Gate, never writes recall conversation state, and preserves all actual
non-relevance blockers: low information, mood/moment interruption, high risk,
cooldowns, and low memory quality.

## Enable and disable

The default is off. The only supported named profile is:

```bash
YOGURT_MEMORY_GATE_EXPERIMENT=v5b7-gate-036
```

Set `YOGURT_MEMORY_GATE_EXPERIMENT=off` or remove the variable to disable it.
Arbitrary numeric thresholds are rejected at service startup. Readiness exposes
only `enabled`, profile name, candidate base score, and a degraded boolean; it
does not expose raw events or identities.

## Audit, privacy, and failure behavior

The independent `memory_gate_experiment_events` table stores only aggregate-safe
decision metadata, score/margin values, candidate kind/layer, explicit flag,
and non-relevance baseline blockers. It has no identity, conversation key,
query, memory text, prompt, or transcript fields. Retention is deterministic:
the newest 10,000 events per named experiment are retained.

Audit evaluation and writes are fail-open. A writer error marks the experiment
degraded in readiness but cannot alter or fail `MemoryService.recall()`.

Use the aggregate-only report:

```bash
PYTHONPATH=social_layer/src python -m yogurt_social.memory_gate_experiment_report \
  --db ~/.openclaw/yogurt-social/memory.sqlite3 \
  --experiment v5b7-gate-036 \
  --json-output outputs/v5b7_gate_shadow.json \
  --report-output outputs/v5b7_gate_shadow.md
```

The report calls a delta `candidate_recovery`, never a correct recall:
**Real shadow traffic is unlabeled. Candidate recovery is not equivalent to
correctness.**

## Promotion criteria and rollback

B.8 or a production threshold proposal requires all of the following review
gates, not an automatic code path:

- Fixed benchmark: false recall 0%; stale, low-info, and high-risk safety 100%.
- At least 500 Gate-evaluated shadow events, including explicit and all three
  candidate kinds where traffic permits.
- Sampled manual review or a labeled evaluation set; unlabeled shadow recovery
  alone cannot establish correctness.

Rollback is only `YOGURT_MEMORY_GATE_EXPERIMENT=off`. The audit table may
remain; no database rollback, client change, or WeChat restart logic is needed.

## Deferred work

This experiment does not enable Real Recall or change the production threshold.
Vector retrieval, sqlite-vec, embeddings, RRF, and reranking remain deferred.
