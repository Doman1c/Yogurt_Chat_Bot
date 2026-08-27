# Memory V5-B.6 Recall Gate Analysis / Calibration

V5-B.6 adds a repeatable, offline analysis harness for the existing V5 Exact +
SQLite FTS candidate path and V4.2 Recall Gate. It uses a new temporary SQLite
database for each run and calls the real `MemoryService`, index, and Gate. It
makes no network connection, reads no user database, and calls no LLM.

No production Recall Gate thresholds or blocker rules were changed in V5-B.6.
Real Recall remains disabled and shadow-only. Vector retrieval, sqlite-vec,
embeddings, RRF, and reranking remain deferred.

## Runbook and dataset integrity

```bash
PYTHONPATH=social_layer/src python -m yogurt_social.memory_gate_analysis \
  --seed social_layer/tests/fixtures/memory_recall_seed.jsonl \
  --cases social_layer/tests/fixtures/memory_recall_cases.json \
  --json-output outputs/memory_gate_analysis_v5b6.json \
  --report-output outputs/memory_gate_analysis_v5b6.md
```

The default run fails closed unless the fixed `memory-recall-v1` hashes match:

- seed: `4e3a3d9ec9b0f30ea9c353bfa6d43d99b50a2cd159164ad4e51a3e1ec4964166`
- cases: `d09709f4c626f1653146dbb084ec385f9d4af1308cb6b8b93c41229712020e1c`

The default JSON and Markdown reports contain synthetic `case_id`, category,
tags, surface, fixture memory IDs, scores, and decisions only. They do not
contain query text, raw/canonical memory text, user identity, conversation key,
or chat transcripts. A separately versioned private fixture can be inspected
only with explicit `--allow-unverified-dataset`; its output maintains the same
metadata-only export policy.

## Baseline and mutually exclusive positive partition

On the fixed 123-case corpus, B.5 remains the reference baseline: Top-1 71.08%,
Recall@3 74.70%, MRR 72.69%, False Recall Rate 0%, and No-Recall Precision
36.70%. B.6 deliberately does not redefine those metrics.

For every positive, B.6 applies this strict, exhaustive order:

1. `pre_gate_positive_skip`
2. `retrieval_miss` — expected/acceptable ID absent from the real index top-8
3. `ranking_miss` — expected is in top-8, but not at rank one
4. `gate_blocked_expected_top` — expected is rank one, but the real Gate blocks
5. `success_allow_expected` — expected is rank one, Gate allows, and it returns
6. `inconsistent_return` — an explicit unexpected state

The first B.6 run has 0, 21, 3, 44, 15, and 0 cases respectively (83 positive
cases total). This separates the 21 retrieval and 3 ranking failures from the
44 clean Gate false negatives; old aggregate `gate_false_negative` counts
should not be used as this partition.

## Gate blockers, margins, and explicit recall

Each clean Gate FN stores production reasons in their original order. B.6 emits
both multi-label and primary counts, plus exact combinations; metadata such as
`candidate_kind:*`, `explicit_recall_request`, and `gate_block` are excluded.
In this corpus, all 44 clean Gate FNs have only
`relevance_below_threshold` as both their multi-label and primary blocker.

For top-candidate-expected positives, the report summarizes score, threshold,
score margin, lexical, continuity, and quality with min/P10/P25/P50/P75/P90/max.
Clean Gate FN margins range from -0.38 to -0.025; one lies 0.01–0.05 below the
threshold, 25 lie more than 0.20 below it, and none already pass threshold but
are held by another blocker.

There are 9 explicit positive cases: 7 clean Gate FNs and 2 retrieval/ranking
misses, with no successful returned recall. The 7 Gate FNs are all relevance
blocks; no explicit case is blocked by low memory quality. Explicit recall
continues to use the production adjustment and does not bypass hard safety
rules.

## Counterfactual threshold sweep

The simulator sweeps base `min_score` from 0.30 to 0.55 in 0.01 increments,
including 0.48. It recomputes only the relevance comparison using the production
formula (explicit -0.12, high-risk +0.10, clamped 0.20–0.95). All other real
blocker state—low-information, emotion, moment, high-risk, conversation and
same-memory cooldown, and quality—is held fixed.

At 0.48, parity with the actual Gate decision is 100% (118/118 Gate-entering
cases), which is a hard precondition to reporting a frontier. The zero-false-
recall safe frontier is 0.36–0.55: every point has False Recall Rate 0% and
100% inactive-stale, low-info, and high-risk safety. At 0.48, correct positive
Gate recall is 15/83 (18.07%). The best zero-FR point is 0.36 with 21/83
(25.30%), a +7.23 percentage-point lift and six additional correct allows.

This is only a candidate for a future controlled V5-B.7 Gate experiment. It is
not a production configuration recommendation or an automatic threshold change.

## One-at-a-time ablation and coverage

At base 0.48, each blocker is separately ignored in an offline simulation.
Ignoring relevance recovers 44 correct positives but introduces 13 false
recalls (32.50% false-recall rate); it is therefore not a safe change. Every
other single ablation recovers zero positives and adds zero false recalls in
this corpus, which is not evidence that those rules are removable.

Coverage is explicit: relevance is exercised by 76 cases, emotion by 5,
moment by 4, and high-risk by 5. `low_information_turn`,
`conversation_cooldown`, `same_memory_cooldown`, and `low_memory_quality` are
each exercised by zero Gate cases, so the report labels them “benchmark has
insufficient coverage for this rule.” New unit fixtures—not the fixed V5-B.5
corpus—are required before judging those rules.

## Recommendation and deferred work

The evidence supports considering a controlled V5-B.7 Gate threshold experiment
at 0.36, while preserving all non-relevance blockers and maintaining the
zero-false-recall guardrails. Retrieval misses still account for 21 positives;
vector candidate generation may be evaluated later, but this change does not
implement it.
