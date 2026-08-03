# Yogurt Chat Bot V3 Research Plan

## Status

```text
Branch: agent/v3-context-semantic
Base: Yogurt Chat Bot V2.1
Plugin baseline: yogurt-social-injector 0.4.0
Companion Tagger branch: agent/v0.4-semantic-index
```

V3 is a research and evaluation line. Stable V2.1 remains on `main` until every production constraint and real-device test passes.

## Primary goals

1. Use a short recent-conversation window instead of only the current user message and final assistant reply.
2. Improve sticker retrieval for paraphrases, synonyms and implicit emotions without making broad Scene labels dominant again.
3. Calibrate the independent send decision with measurable precision and recall rather than hand-tuning isolated prompts.
4. Keep decisions explainable through structured reasons, component scores and candidate margins.
5. Preserve one chat-model inference in `auto` mode.

## Non-negotiable constraints

- VLM stays offline in Yogurt Sticker Tagger.
- No second chat-model inference in normal chat.
- The model cannot choose arbitrary files or paths.
- Only `approved + enabled + content_warnings=["none"]` assets may be sent.
- High-risk conversations never receive stickers.
- Media failure never blocks the text reply.
- Cooldown is committed only after confirmed media success.
- Private conversation text is not silently stored as training data.
- V3 changes remain behind an explicit feature flag until shadow evaluation passes.

## Proposed architecture

```text
Recent conversation window
+ current user message
+ final assistant reply
+ V1 Scene / Mood
        ↓
V3 Send Feature Extractor
        ↓
Calibrated Send Decider
        ├── hard safety gates
        ├── conversational fit
        ├── emotional intensity
        ├── reply expression
        ├── repetition / cooldown penalties
        └── candidate quality requirement
        ↓
Hybrid Sticker Retriever
        ├── exact phrases and keywords
        ├── emotion / tone / description / OCR
        ├── character n-gram lexical similarity
        ├── optional lightweight embedding backend
        └── deterministic reranker
        ↓
Existing TypeScript delivery policy
        ├── threshold and margin
        ├── cooldown and hourly limit
        └── outbound adapter delivery
```

## Runtime retrieval candidates to evaluate

### A. Enhanced deterministic lexical retrieval

- exact phrase and normalized synonym tables;
- Chinese character n-grams;
- no model call;
- lowest latency and easiest rollback.

### B. Offline vector index plus lightweight runtime embedding

- Tagger generates retrieval text and sticker vectors;
- runtime embeds only the cleaned conversation query;
- embedding is not a chat-model inference, but adds latency and memory;
- must be benchmarked on the production M1 16 GB Mac before selection.

### C. Same-inference structured intent output

- the normal Qwen response includes a hidden bounded sticker intent field;
- no second inference;
- depends on reliable OpenClaw structured-output integration;
- must not expose paths or allow the model to bypass safety policy.

No backend is selected before evaluation.

## Evaluation dataset

Each case must record two labels:

```text
should_send
expected_sticker_intents / acceptable_sticker_ids
```

Recommended fields:

```json
{
  "id": "v3-case-001",
  "conversation": [
    {"role": "user", "text": "..."},
    {"role": "assistant", "text": "..."}
  ],
  "current_user_message": "...",
  "assistant_reply": "...",
  "relationship": "owner",
  "expected_should_send": true,
  "acceptable_intents": ["speechless"],
  "forbidden_ids": [],
  "notes": ""
}
```

The committed dataset contains synthetic prompts only. Real private conversations require explicit manual redaction before being added.

## Metrics

### Send decision

- precision: avoid intrusive or inappropriate stickers;
- recall: recognize natural opportunities without explicit requests;
- false-positive rate for technical, serious and high-risk cases;
- send frequency under cooldown simulation.

### Selection

- Top-1 acceptable accuracy;
- Top-3 recall;
- mean reciprocal rank;
- generic-sticker domination rate;
- Scene-overrides-specific-intent error rate.

### Runtime

- median and P95 added latency;
- memory impact on M1 16 GB;
- failure and fail-open behavior;
- number of additional model calls.

## Implementation phases

### V3.0a — Evaluation foundation

- add versioned synthetic evaluation cases;
- add a CLI evaluator for V2.1 baseline results;
- export structured per-case decision and ranking traces;
- define pass/fail thresholds before changing production behavior.

### V3.0b — Conversation-window features

- accept the most recent 3–5 bounded turns;
- derive repetition, emotional continuity and reply-fit features;
- keep raw conversation text ephemeral.

### V3.0c — Hybrid retrieval experiment

- compare exact/keyword baseline, character n-gram similarity and one lightweight embedding candidate;
- use the same deterministic reranker and safety filters;
- produce latency and ranking reports.

### V3.0d — Shadow mode

- V2.1 remains responsible for real sending;
- V3 records only `would_send`, Top-K and reasons;
- no V3 cooldown mutation and no duplicate media delivery.

### V3.0e — Controlled production trial

- explicit feature flag;
- immediate rollback to V2.1;
- real-device tests for casual, comfort, playful, celebration, technical, serious and high-risk cases.

## Initial acceptance targets

These are provisional and must be measured on the committed evaluation set:

```text
technical / serious / high-risk false-positive rate = 0
send precision >= 0.90
Top-1 acceptable selection accuracy >= 0.85
Scene-overrides-specific-intent errors = 0
P95 added latency <= 250 ms for deterministic backend
```

Embedding-based experiments require a separate latency target after benchmarking.

## First engineering task

Build the V3.0a evaluator against the unchanged V2.1 runtime. Do not alter real sending behavior in the first V3 commit.
