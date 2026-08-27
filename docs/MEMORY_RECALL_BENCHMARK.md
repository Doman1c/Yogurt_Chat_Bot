# Yogurt Memory V5 Recall Benchmark

## Purpose

V5-B.5 measures the existing V5-B pipeline before any retrieval technology is changed:

```text
canonical SQLite → Exact + FTS5 → V4.2 Recall Gate → shadow decision
```

It is offline, uses an isolated temporary SQLite database, and does not call an LLM or network service. It is a diagnostic harness, not a production memory importer, and it does not inject memory into a prompt.

## Dataset and privacy

The committed `social_layer/tests/fixtures/memory_recall_seed.jsonl` is a sanitized project-oriented seed. The 123 cases in `social_layer/tests/fixtures/memory_recall_cases.json` use schema version 1 and dataset version `memory-recall-v1`.

Seed records are `fact`, `preference`, or `episode`. Cases have a query, expected opaque memory IDs, decision expectation, surface, category, and tags. The loader rejects credentials, private keys, identifiers, and other obvious sensitive canonical values. It saves neither chat transcripts nor raw prompt history. A real private seed belongs under `private/memory_benchmark/` (which is gitignored) and can be supplied with `--seed`; never commit it without explicit approval.

Add cases with stable IDs and a specific category. Do not turn a failed case into an entity-specific retrieval rule: record it and compare the next provider against the same corpus.

## Run

```bash
PYTHONPATH=social_layer/src python -m yogurt_social.memory_benchmark \
  --seed social_layer/tests/fixtures/memory_recall_seed.jsonl \
  --cases social_layer/tests/fixtures/memory_recall_cases.json \
  --json-output outputs/memory_benchmark_v5b.json \
  --report-output outputs/memory_benchmark_v5b.md
```

`--fail-on-regression` compares the run to `social_layer/tests/baselines/memory_recall_v5b.json`. The protection line is false recall rate at most 5%, plus 100% inactive-stale safety, low-information pre-gate behavior, and high-risk suppression. It deliberately does not require perfect paraphrase or cross-language recall.

The harness runs the real `MemoryService.recall()` decision. It separately uses the existing derived index for candidate ranking, so Top1/Recall@3/MRR measure retrieval while allow/block measures the gate.

## Metrics

- **Top1 Accuracy:** among `should_recall` cases, expected/acceptable ID is the first ranked candidate.
- **Recall@3:** expected/acceptable ID occurs in the first three candidates.
- **MRR:** reciprocal rank of the first expected/acceptable candidate.
- **False Recall Rate:** `should_recall=false` cases whose final gate decision is `allow`.
- **No-Recall Precision:** among all final non-allow decisions, the fraction that were truly `should_recall=false`.
- **Recall Decision Rate:** share of all cases whose final decision is allow.

False Recall Rate and No-Recall Precision are intentionally more important than ranking recall for a private companion. Category rows show whether a gap is retrieval, ranking, or gate related.

## Shadow audit

The gate writes identity-free event metadata (decision, candidate kind, score, reasons, and scope) without raw user messages or memory text. Summarize it locally:

```bash
PYTHONPATH=social_layer/src python -m yogurt_social.memory_shadow_audit \
  --db ~/.openclaw/yogurt-social/memory.sqlite3 --days 7
```

`--since` and `--until` accept ISO-8601 timestamps. The output contains only turn count, allow rate, decision/block reasons, candidate kind, score buckets, and surface split. It omits user IDs, conversation keys, prompts, and canonical memory text.

## Future comparison

V5-C may evaluate a vector candidate generator only if this fixed corpus shows that Exact/FTS has a retrieval gap while the gate and false-recall safeguards remain healthy. A future provider must reuse these seed and case hashes; vector retrieval, RRF, reranking, and real prompt recall are not part of V5-B.5.
