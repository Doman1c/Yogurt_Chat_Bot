# Yogurt Chat Bot（酸奶）

酸奶是一个运行在 **OpenClaw + Ollama + 微信官方 Bot** 之上的本地 AI 角色项目。

项目尽量保持聊天主链路轻量：正常聊天只进行一次聊天模型推理，自动记忆、多气泡和在线表情决策不会再调用第二个聊天模型，实时聊天也不会调用视觉模型。

## 当前开发版本

```text
Yogurt Chat Bot: V4.3.2 hardening
OpenClaw plugin: yogurt-social-injector 0.6.3-alpha.6
Plugin entry: integrations/openclaw/index-v43.ts
Sticker Runtime: V2.1
Catalog Schema: 1.0
```

当前 V4.3.2 仍在 Draft PR 中，Feature Flags 默认关闭；在实机回归完成前不应直接开启为默认行为。

## 核心能力

- V1：社交场景、人格与稳定记忆上下文；
- V2.1：最终回复上的表情决策、安全过滤与本地图片发送；
- V3：确定性自动记忆、自然多气泡；
- V4.0：会话状态；
- V4.1：Response Planner；
- V4.2：记忆召回 Shadow Gate；
- V4.3：统一 Outbound Intent / Result、Shadow 与 Enforced Coordinator；
- V4.3.1：强 Turn Correlation、结构化 Planner 交接、类型化 Sticker Lifecycle、typed media / final payload 保护；
- V4.3.2：跨 Hook 强 ID 命名空间兼容、弱 child 防抢占、V3 memory/multi-bubble pending Turn 同步强关联。

## 当前架构

```text
微信官方 Bot 私聊
  ↓
@tencent-weixin/openclaw-weixin
  ↓
OpenClaw before_prompt_build
  ↓
Yogurt Social Injector
  ├── V1 Social Layer
  │   ├── Scene / Mood / Relationship
  │   └── 稳定记忆上下文
  │
  ├── V2.1 Sticker Runtime
  │   ├── 离线 Catalog
  │   ├── pre-reply 候选准备
  │   ├── post-reply Send Decider
  │   ├── Ranker / Delivery Policy
  │   └── typed Sticker Lifecycle
  │
  ├── V3 Runtime
  │   ├── 自动记忆候选
  │   ├── legacy natural multi-bubble
  │   └── strong Turn-correlated pending stores
  │
  ├── V4 State / V4.1 Planner / V4.2 Recall Gate
  │   ├── ConversationState
  │   ├── ResponsePlan Prompt Context
  │   └── Structured ResponsePlan Snapshot
  │
  └── V4.3.2 Outbound Coordinator
      ├── cross-hook strong Turn correlation
      ├── Shadow comparison
      ├── optional Enforced bubble target
      ├── typed media / final reply guard
      └── Sticker terminal OutboundResult
  ↓
Ollama / Qwen：一次聊天推理
  ↓
OpenClaw outbound hooks
  ↓
微信 sendText / sendMedia
```

## 为什么 V4.3.2 需要 Turn Correlation

同一微信会话可能在前一轮 Sticker 还没发送时立刻进入下一轮。如果只用：

```text
session / sender / peer
```

作为单值 key，后一轮会覆盖前一轮。

V4.3.1 开始优先使用：

```text
runId / messageId / traceId
```

V4.3.2 进一步处理 OpenClaw 在不同 Hook 阶段暴露不同强 ID 命名空间的情况。例如：

```text
before_prompt_build: traceId=T1
message_sending:     runId=R1
recursive child:     只有 session/peer
```

`traceId=T1` 与 `runId=R1` 不是冲突，因为它们属于不同命名空间。只有同一命名空间明确不一致，例如 `run:A` 对 `run:B`，才是硬冲突。

当前规则：

- 强 ID 精确命中优先；
- `traceId → runId`、`messageId → runId` 等不同强命名空间可在唯一弱父级身份下兼容关联；
- `run:A → run:B`、`trace:A → trace:B` 等同命名空间不一致绝不回退；
- 已经带强身份的父 Turn 不能被只有弱 session/peer 的递归 child 抢走；
- 早期 Hook 完全没有强 ID 时，后续强 ID Hook 仍可在唯一候选下兼容关联；
- 多个候选时永远拒绝猜测；
- V3 自动记忆、多气泡 pending Turn 与扩展记忆现在使用同一 CorrelationStore。

## Response Planner 与 Outbound Coordinator

V4.1 Planner 输出：

```json
{
  "plan_version": "4.1",
  "dialogue_act": "plan",
  "primary_goal": "advance_current_project",
  "reply_length": "medium",
  "bubble_target": 2,
  "ask_followup": false,
  "sticker_preference": "neutral",
  "state_version": 67
}
```

V4.3.1 起不再从中文 Prompt Context 反向正则解析这些字段，而是通过结构化 Store 直接交给 Coordinator。因此 V4.3 日志中的 `stateVersion` 应与 Planner 的真实版本一致，不再固定为 `0`。

Coordinator 当前主要执行 `bubble_target`，其余 Planner 字段暂时用于观测和后续扩展。

## 多气泡规则

适合微信私聊的短回复可以拆成 2～3 个自然气泡。

默认不强拆：

- 代码块；
- Markdown 列表；
- 链接；
- 结构化文本；
- 太短或太长的内容；
- 无法形成足够自然边界的文本；
- 已携带媒体的消息。

V4.3 Enforced 模式只在安全边界恰好能够满足 Planner 目标时直接执行，否则 fail-open 到 legacy V3 或原父消息。

旧 V3 返回 `undefined` 时，V4.3 会显式返回：

```json
{
  "content": "<original assistant text>",
  "cancel": false
}
```

防止宿主把“没有 Hook 修改”错误地表现为空回复。

如果子气泡已经部分发送后失败，则取消父消息，避免用户同时收到“部分子气泡 + 完整整段”的重复回复。

## Sticker Runtime

Sticker Catalog 和图片资产由独立的离线流程维护；实时聊天不调用 VLM。

在线流程：

```text
用户消息
  ↓
pre-reply scene / candidate preparation
  ↓
Qwen 生成完整正文
  ↓
post-reply Send Decider
  ↓
Ranker / Delivery Policy
  ↓
selected / scheduled
  ↓
文字回复
  ↓
图片媒体发送
  ↓
sent / skipped / failed
```

V4.3.1 使用类型化 Sticker Lifecycle：

```text
selected
scheduled
sent
skipped
failed
```

业务关联不再依赖重新解析日志字符串。

V4.3 父级结果示例：

```text
v4.3 outbound shadow ... sticker=pending stickerReason=delivery_scheduled ...
v4.3 outbound finalized mode=shadow ... sticker=sent stickerReason=media_sent ... finalStage=sticker
```

明确不发：

```text
sticker=skipped
```

媒体失败：

```text
sticker=failed
```

Sticker Lifecycle 事件只携带有限状态、原因码、Sticker ID 和 Turn aliases，不携带用户/助手正文。

## 自动记忆

V3 使用确定性规则提取有限类型的长期信息，不调用第二个模型。

支持：

- 用户事实；
- 用户偏好；
- 重要里程碑；
- 重复确认；
- 新旧事实冲突；
- 晋升 / 合并 / conflict；
- 不可变修订记录。

不会保存密码、Token、验证码、密钥、身份证件或支付信息，也不会建立原始聊天全文表。

## V4 会话状态与召回

ConversationState 维护有限的：

- current topic；
- dialogue act；
- mood；
- interaction mode；
- open threads；
- recent actions；
- state version。

V4.2 Recall Gate 目前保留 Shadow-first 设计：先观察是否应该召回，再决定未来是否接管实际记忆注入。

## Feature Flags

新功能默认关闭：

```json
{
  "stickerEnabled": false,
  "autoMemoryEnabled": false,
  "multiBubbleEnabled": false,
  "conversationStateEnabled": false,
  "responsePlannerEnabled": false,
  "memoryRecallGateEnabled": false,
  "unifiedOutboundShadowEnabled": false,
  "unifiedOutboundEnforceEnabled": false
}
```

V4.3 Shadow / Enforced 需要 `responsePlannerEnabled=true` 才有意义。

## 安全与 fail-open

- Python/SQLite/Bridge 异常不阻断原始聊天；
- 新功能默认关闭；
- 不保存原始完整聊天记录作为调度状态；
- 强关联失败或歧义时宁可不协调，也不会猜另一个 Turn；
- Sticker Catalog、图片资产与本地运行配置不由升级脚本清理或覆盖；
- Coordinator 失败时保留 legacy V3 / 原始父消息路径。

## 当前验证重点

V4.3.2 合并前需要在真实微信上确认：

1. Planner、V4.3 Intent 和 Shadow 的 `stateVersion` 一致且非零；
2. `traceId`/`runId` 跨 Hook 时，递归弱 child 不再抢走父 Intent；
3. Planner `bubbles=2` 且 legacy 实际发送 2 条时，Shadow 最终记录 `actualBubbles=2 matched=true`；
4. 同一微信 session 快速连续两个 Turn 不串 Planner/V3 pending state；
5. Turn A Sticker pending 时启动 Turn B，A 的 terminal Sticker 仍回写 A；
6. Enforced target1/target2 基本回归；
7. 两个 V4.3 flags 关闭后完全回到 legacy 路径。

PR #8 在上述实机验证结束前继续保持 Draft，不自动合并。
