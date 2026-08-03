# Yogurt Sticker V2.1 Design

## Goal

V2.1 separates two questions that V2 mixed together:

1. Should Yogurt send a sticker in this turn?
2. If yes, which approved sticker best matches the conversation?

The release candidate preserves the production constraints: one chat-model inference, no realtime VLM, Catalog-only local files, fail-closed warnings, text-preserving media failures, and WeChat delivery through the channel outbound adapter.

## Runtime flow

```text
before_prompt_build
  → V1 social analysis
  → save a bounded turn snapshot
  → Qwen generates one normal reply
agent_end / message_sent
  → retrieve again with user message + final assistant reply
  → StickerSendDecider
  → semantic-first Top-K ranking
  → cooldown/rate policy
  → outbound adapter sends one image
```

`message_sent` is the fallback when the OpenClaw `agent_end` payload does not expose final assistant text.

## Send decision

- `technical` and `serious` are hard blocked.
- `comfort` and `casual` can enter the decision pipeline.
- high-risk comfort language is hard blocked.
- celebration and playful have lower proactive thresholds.
- neutral casual chat normally remains below threshold.
- candidate quality and Top1/Top2 margin contribute to the decision.
- final assistant reply can add emotional intent or suppress a sticker when the reply is long or technical.

This decision does not replace cooldowns. TypeScript still enforces the global, scene, per-sticker and hourly limits.

## Ranking

V2.1 prioritizes specific meaning over broad scene labels:

```text
user keyword       +5.0
assistant keyword  +2.5
emotion             +4.0
description hit     +2.5
OCR hit             +1.5
tone                 +1.0
scene                +1.0
relationship         +0.5
weight prior         [-0.5, +0.5]
confidence prior     [-0.25, +0.25]
```

`weight` is no longer multiplicative.

## Explicit requests

- `explicit_specific`: no unrelated fallback and no artificial score floor.
- `explicit_generic`: may select one safe context candidate and apply the ordinary explicit threshold.
- all safety, relationship and path filters remain mandatory.

## Observability

Every candidate exposes `matched_signals` and `score_breakdown`. Runtime logs include:

```text
send decision ... shouldSend=... score=... threshold=...
top=<id>:<score> runnerUp=<id>:<score>
post-reply sticker selected ...
```

The Tagger V0.3 decision laboratory consumes the same Python ranking and decision modules, so browser simulations and production use the same algorithm.

## Real-device acceptance focus

Test without asking for a sticker:

- celebration: `终于跑通啦！`
- playful/speechless: `这也太离谱了吧`
- comfort: `我今天真的好累`
- affectionate/casual: `我回来啦，想你了`
- neutral casual: `今天路上人挺多的`
- technical: `接口报错了，怎么查？`

Expected behavior is not “always send”. The decision and selected image should match the conversational intent while neutral and technical turns stay quiet.
