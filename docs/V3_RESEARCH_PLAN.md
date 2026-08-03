# Yogurt Chat Bot V3 Research Plan

## Status

```text
Branch: agent/v3-context-semantic
Base: Yogurt Chat Bot V2.1
Plugin alpha: yogurt-social-injector 0.5.0-alpha.1
Companion Tagger branch: agent/v0.4-semantic-index
```

V3 is a research and evaluation line. Stable V2.1 remains on `main` until every production constraint and real-device test passes.

Current V3 alpha includes two opt-in experiments:

```text
Automatic memory candidate pipeline
Natural multi-bubble WeChat delivery
```

Both remain disabled by default.

## Primary goals

1. Use a short recent-conversation window instead of only the current user message and final assistant reply.
2. Add automatic memory without silently treating every chat sentence as a permanent fact.
3. Improve sticker retrieval for paraphrases, synonyms and implicit emotions without making broad Scene labels dominant again.
4. Calibrate the independent send decision with measurable precision and recall rather than hand-tuning isolated prompts.
5. Make short emotional replies feel more human through bounded multi-bubble delivery.
6. Preserve one chat-model inference in `auto` mode.

## Non-negotiable constraints

- VLM stays offline in Yogurt Sticker Tagger.
- No second chat-model inference in normal chat.
- Automatic memory extraction does not call another language model in the first alpha.
- Only first-person user statements may create memory candidates.
- Assistant text is never promoted as a user fact.
- Passwords, tokens, verification codes, keys, identity document numbers and payment data are never extracted.
- The model cannot choose arbitrary files or paths.
- Only `approved + enabled + content_warnings=["none"]` assets may be sent.
- High-risk conversations never receive stickers.
- Media failure never blocks the text reply.
- Cooldown is committed only after confirmed media success.
- Raw private conversation text remains ephemeral unless the user explicitly promotes a bounded memory.
- V3 changes remain behind explicit feature flags until shadow evaluation passes.

## V3 automatic memory alpha

### Pipeline

```text
Completed chat turn
  ↓
Use the user's message only for extraction
  ↓
Deterministic first-person candidate extractor
  ├── facts
  ├── preferences
  └── personal milestones
  ↓
Sensitive-data block
  ↓
Importance and confidence scoring
  ↓
Candidate table
  ↓
Conflict resolver
  ├── same value → reaffirm / merge
  ├── explicit update → update old memory
  ├── uncertain conflict → keep as conflict candidate
  └── low importance → keep pending
  ↓
Stable facts / preferences / episodes
  + immutable revision audit
```

### Candidate states

```text
pending
promoted
merged
conflict
```

- `pending`: plausible but not important or repeated enough.
- `promoted`: inserted into stable memory or used to update an old memory.
- `merged`: confirms an existing memory without creating a duplicate.
- `conflict`: contradicts an old memory but lacks a strong update signal.

### Importance behavior

Importance uses explicit and explainable signals:

- direct first-person wording;
- stable markers such as `长期`, `常住`, `以后`, `最喜欢`;
- explicit update markers such as `改成`, `换成`, `现在是`;
- repeated confirmation of the same candidate;
- temporary markers such as `今天`, `刚刚`, `暂时` reduce importance.

Default automatic promotion threshold:

```text
0.78
```

Personal milestones use a stricter effective threshold so ordinary one-off chat does not flood episode memory.

### Storage

The existing SQLite database gains:

```text
memory_candidates
memory_revisions
```

`memory_candidates` stores only bounded normalized candidate content and a source hash. It does not store a raw transcript.

`memory_revisions` records inserts, reaffirmations and updates so old memory changes remain auditable.

## V3 multi-bubble alpha

### Goal

Turn a reply such as:

```text
主人最厉害啦喵！🥰✨（星星眼）

不过比起称赞，酸奶更爱崇拜主人的成就喵～💗🌟
```

into two real WeChat text messages instead of one large bubble.

### Delivery architecture

```text
Qwen generates one complete reply
  ↓
OpenClaw message_sending hook
  ↓
Natural text splitter
  ├── blank paragraphs first
  ├── then sentence boundaries
  ├── maximum 2–4 bubbles
  └── merge tiny fragments
  ↓
Tencent WeChat adapter sendText
  ↓
Cancel the original unsplit delivery
```

This does not add another model inference.

### Messages that are not split

- very short replies;
- code fences;
- Markdown lists and headings;
- tables;
- links;
- long technical responses;
- media messages;
- replies outside the configured WeChat channel.

### Failure behavior

- If the first bubble cannot be sent, the original unsplit reply is allowed through.
- If a later bubble fails after earlier bubbles were sent, the original is cancelled to avoid duplicate text and the partial result is logged.
- Recursive `message_sending` calls from `sendText` are guarded in memory.

## Feature flags

```json
{
  "autoMemoryEnabled": false,
  "autoMemoryPromotionThreshold": 0.78,
  "autoMemoryTimeoutMs": 2500,
  "multiBubbleEnabled": false,
  "multiBubbleMinChars": 24,
  "multiBubbleMaxChars": 360,
  "multiBubbleMaxBubbles": 3,
  "multiBubbleDelayMs": 420
}
```

Stable V2.1 behavior remains unchanged while both flags are `false`.

## Conversation and sticker architecture

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

No retrieval backend is selected before evaluation.

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

### Automatic memory

- extraction precision;
- stable-memory promotion precision;
- conflict detection recall;
- duplicate merge accuracy;
- sensitive-data false acceptance rate;
- candidate-to-stable promotion rate;
- update audit completeness.

### Multi-bubble

- percentage of casual replies split into 2–3 bubbles;
- structured/technical false split rate;
- duplicate-delivery rate;
- partial-delivery rate;
- added delivery latency.

### Sticker send decision

- precision: avoid intrusive or inappropriate stickers;
- recall: recognize natural opportunities without explicit requests;
- false-positive rate for technical, serious and high-risk cases;
- send frequency under cooldown simulation.

### Sticker selection

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

### V3.0a — Automatic-memory and delivery foundation

- candidate and revision tables;
- deterministic extraction;
- conflict and update logic;
- importance scoring;
- opt-in multi-bubble sender;
- Python 3.9–3.12 and Node 22 regression tests.

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

- explicit feature flags;
- immediate rollback to V2.1;
- real-device tests for automatic memory, multi-bubble text, casual, comfort, playful, celebration, technical, serious and high-risk cases.

## Initial acceptance targets

```text
sensitive-data false acceptance rate = 0
technical / serious / high-risk sticker false-positive rate = 0
automatic memory promotion precision >= 0.95
multi-bubble structured-response false split rate = 0
multi-bubble duplicate-delivery rate = 0
sticker send precision >= 0.90
Top-1 acceptable sticker accuracy >= 0.85
Scene-overrides-specific-intent errors = 0
P95 deterministic feature latency <= 250 ms
```

Embedding-based experiments require a separate latency target after benchmarking.

## Current engineering task

Run the V3 alpha test matrix, fix cross-version regressions, then perform a controlled Mac test with both feature flags enabled. Do not merge V3 into `main` before real-device rollback and privacy checks pass.
