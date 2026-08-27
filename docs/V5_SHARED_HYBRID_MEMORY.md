# Yogurt Memory V5 — Shared Hybrid Memory

## Status

V5-A and V5-B are implemented as a loopback-only, shadow-only service. It wraps the existing
V3 deterministic writer and V4.2 Recall Gate; canonical SQLite remains the
source of truth. Vector retrieval is intentionally deferred: this release ships
structured exact lookup plus a rebuildable SQLite FTS5 derived index.

## Architecture and audit

```text
WeChat / Native client
        ↓  short timeout, fail-open
Memory Service (127.0.0.1:17621)
        ├─ facts/preferences/episodes/candidates/revisions (canonical SQLite)
        ├─ memory_search_documents + FTS5 (derived)
        └─ conversation_states (temporary)
```

V3 writes from the OpenClaw post-reply hook through `AutoMemoryStore`, including
candidate occurrence, promotion, merge, conflict, and revision behavior. V4.2
was shadow-only and remains the final relevance, risk, emotional-interruption,
quality, continuity, and cooldown gate. Its old scope was `user_id +
conversation_key`; V5 converts state scopes to `surface:conversation_key` and
updates that scoped state with every recall turn after the low-information gate.

Durable identity is `persona_id + user_id`. The legacy schema has only
`user_id`, so the default `persona_id=yogurt` keeps existing keys such as
`owner` (zero-copy migration); non-default personas use `persona:user`.

## API, privacy, and token budget

- `GET /v1/health`, `GET /v1/ready`
- `POST /v1/memory/recall`
- `POST /v1/memory/observe`
- `GET /v1/memory/snapshot`, `/candidates`, `/revisions` (loopback admin only)

Recall accepts identity, surface, conversation key, message, up to 3 items and
a hard maximum of 350 tokens (220 default). It returns only compact prompt text,
source id, score, and token estimate—not raw state, full Prompt, or transcript.
The pipeline is `pre-gate → Exact/FTS → V4.2 gate → top 1 compact item`.
Acknowledgements such as `好的`, `嗯`, `OK`, and `继续` skip FTS and audit work.

## SQLite, migration, rollback

Every memory/state connection enables foreign keys, WAL, and `busy_timeout=3000`.
No canonical table is reset or destructively migrated. The V5 schema-version
table covers only derived data. Rebuild replaces one identity's FTS documents
from active canonical rows, so RTX 3070 → RTX 4080 cannot return an active 3070.
The FTS external-content delete carries the old indexed `search_text`, so stale
tokens are removed before a rebuilt document can reuse its row id. Canonical
write success is reported independently from derived-index success:
`canonical_write=true`, `index_updated=false`, and `index_dirty=true` are still
a successful observe result. Idempotency keys persist bounded result metadata,
identity, and scope—never raw chat text—and prevent retries from increasing V3
occurrences or revisions.

OpenClaw fields default to disabled. It is shadow-only and logs only decision,
kind, source id, and token count; it never injects V5 memory into a Prompt. Set
`memoryServiceEnabled=false` for immediate rollback; timeouts, invalid replies,
or any SQLite/FTS/service failure skip memory and preserve normal Qwen reply.

## Deployment and future work

Install `social_layer/requirements-memory-service.txt`, then run
`scripts/yogurt-memory-service` with `YOGURT_SOCIAL_DB` set. Enable OpenClaw
with `memoryServiceEnabled=true` and the loopback URL. The startup script
enforces `127.0.0.1` and ignores any host override. OpenClaw distinguishes a
successful service write, a definite `ECONNREFUSED` (safe local V3 fallback),
and an ambiguous outcome such as timeout or malformed response (no fallback,
which prevents duplicate canonical writes). Native/Desktop/Live2D/VoiceSession use the
same Python client and their own `native:*` state scope. WebUI remains an admin
client rather than a second memory implementation.

`test_memory_service.py` and CI cover sharing, scope isolation/current-turn
state, pre-gate, emotion/high-risk/unrelated blocking, WAL, loopback client,
idempotent observe, index failure, FTS stale-token deletion, token consistency,
and FastAPI HTTP smoke endpoints. Observe first claims a persistent
`processing` record under `BEGIN IMMEDIATE`; a concurrent or post-crash retry
does not replay a canonical write. Processing records are intentionally retained
for now (TTL/max-row pruning is future maintenance work). Real Recall is deferred until benchmark and
shadow-audit acceptance. Before vector work, add the versioned
approximately-100-case corpus and report Top-1, Recall@3/5, MRR, false-recall
rate, and no-recall precision. Vector must be a provider-abstracted, rebuildable
derived index; future source fusion uses RRF before the V4.2 gate.

## V5-B.5 benchmark and shadow audit

V5-B.5 adds an offline, versioned recall baseline with a sanitized corpus,
real `MemoryService.recall()` execution, category metrics, latency reporting,
and regression protections for false recalls, inactive rows, low-information
turns, and high-risk suppression. It also adds an aggregate-only audit reader
for existing shadow events. See [Memory Recall Benchmark](MEMORY_RECALL_BENCHMARK.md).
Neither Vector/RRF nor Real Prompt Recall has been implemented.

## V5-B.6 Recall Gate analysis

V5-B.6 keeps the B.5 corpus fixed and adds an offline, isolated-SQLite analysis
tool for the real service/index/Gate path. It partitions positive failures into
pre-gate, retrieval, ranking, clean Gate-blocked, success, and inconsistent
states; reports privacy-safe candidate diagnostics; and runs threshold sweeps
and one-at-a-time blocker ablations without changing production behavior. The
service remains shadow-only, Real Recall remains closed, Vector/RRF remains
unintroduced, and the production threshold is unchanged. The next phase must
be determined by these benchmark results; see
[Recall Gate Analysis](MEMORY_RECALL_GATE_ANALYSIS.md).
