# Yogurt V4.3 / V4.3.1 Unified Outbound Coordinator

V4.3 解决 V4.1 实机测试中暴露的结构性差异：

```text
Response Planner 建议 bubbles=1
V3 multi-bubble 实际发送 count=3
```

问题不在 Planner 或拆分器单独失效，而在于两者分别做决定，缺少同一份可执行、可审计的发送计划。

V4.3.1 是合并前加固版本，重点修复快速连续消息时的 Turn 关联、Planner 结构化交接、Sticker 终态关联和 typed media 边界。

## 目标

在不增加第二次聊天模型推理的前提下，把以下行为收敛到一个可检查、可回滚的 Outbound Plan：

- 回复规划器建议的气泡目标；
- 实际文本拆分结果；
- 表情倾向与最终图片结果；
- 单条、拆分、部分发送和 fail-open 降级；
- 计划值与实际值的差异审计；
- 同一微信会话内并发/交错 Turn 的稳定关联。

## 非目标

V4.3 / V4.3.1 不负责：

- 生成新的聊天正文；
- 调用第二个聊天 LLM；
- 在线调用 VLM；
- 修改 Sticker Catalog 评分、阈值或素材；
- 保存用户原文或助手完整回复到 Outbound Store；
- 自动裁剪回复以强行满足 Planner 的长度建议。

## 数据模型

### Structured ResponsePlan Snapshot

V4.3.1 不再从中文 `prependContext` 反向解析 Planner 数据。V4.1 observer 在生成 Prompt Context 的同时，把白名单结构化字段写入独立 Turn Store：

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

Prompt Context 只用于给模型阅读；Coordinator 直接消费上述结构化 Snapshot。因此 `stateVersion` 不再固定为 `0`。

### OutboundIntent

Coordinator 只保存执行与审计需要的有限字段：

```json
{
  "planVersion": "4.1",
  "bubbleTarget": 2,
  "stickerPreference": "neutral",
  "replyLength": "medium",
  "stateVersion": 67,
  "source": "response_planner"
}
```

不保存用户消息、助手回复或完整 Prompt。

### OutboundResult

```json
{
  "actualBubbleCount": 2,
  "textChars": 88,
  "outcome": "split",
  "stickerOutcome": "sent",
  "stickerReason": "media_sent"
}
```

`stickerOutcome` 只允许：

- `pending`：已选中/安排发送，等待媒体终态；
- `sent`：媒体适配器确认发送；
- `skipped`：策略明确决定不发；
- `failed`：已经进入选择或发送链路，但最终失败；
- `unknown`：仅允许内部中间构造，不应作为 V4.3 最终观测结果。

`textChars` 只保存长度；`stickerReason` 使用受限字符集和长度。

## Turn Correlation

### 为什么不能只用 session / peer alias

同一微信私聊可能出现：

```text
Turn A：正文完成，Sticker 延迟发送中
Turn B：用户马上又发一条消息
Turn B：正文先完成
Turn A：Sticker 最后发送
```

如果 Store 只允许：

```text
session:xxx -> 当前唯一 Turn
```

Turn B 会覆盖 Turn A，导致 Planner 或 Sticker 终态串轮。

### V4.3.1 CorrelationStore

强关联字段优先级：

```text
runId / messageId / traceId
```

同时保留：

```text
session / sender / peer / account / conversation
```

作为兼容弱别名。

规则：

1. 有强 ID 时优先精确匹配；
2. 不同强 ID 即使共享同一 session/peer，也可同时存在；
3. 强 ID 查询不允许落到另一个带不同强 ID 的 Turn；
4. 如果较早 Hook 完全没有强 ID，而后续 Hook 才出现强 ID，只在弱别名唯一且无歧义时兼容回退；
5. 多个候选共享弱别名时拒绝猜测；
6. Store 有 TTL 和容量上限；
7. 计划消费仍为一次性。

这一规则应用到：

- ResponsePlan Snapshot；
- OutboundIntent；
- V4 assistant-state pending turn；
- V2.1 Sticker turn；
- Sticker candidate snapshot；
- V3 pre-bubble Sticker Finalizer context；
- V4.3 Sticker signal / pending OutboundResult。

## Sticker Lifecycle

### V4.3d alpha.4

alpha.4 首次实现：

```text
selected/scheduled
→ sticker=pending
→ media sent/failed
→ v4.3 outbound finalized
```

实机已经验证 `pending → sent` 和 `skipped`。

### V4.3.1 alpha.5

alpha.4 仍通过解析现有日志字符串来观察 Sticker 终态。alpha.5 改为类型化内部事件：

```ts
type StickerLifecyclePhase =
  | "selected"
  | "scheduled"
  | "sent"
  | "skipped"
  | "failed";
```

V2.1 Sticker Runtime 在原有选择/发送位置直接 emit 生命周期事件；V4.3 订阅事件并更新同一父 Turn 的 `OutboundResult`。

因此：

- 日志文案变更不会破坏业务关联；
- logger 不再承担程序通信；
- 真实 `stickerId` 可用于防止终态串到另一张图片；
- delayed media send 捕获原 Turn aliases；
- listener 异常不会阻断文字或图片发送；
- 45 秒仍未获得终态时记录 `delivery_outcome_timeout`。

示例：

```text
v4.3 outbound shadow ... sticker=pending stickerReason=delivery_scheduled ...
sticker media sent ...
v4.3 outbound finalized mode=shadow ... sticker=sent stickerReason=media_sent ... finalStage=sticker
```

明确跳过：

```text
v4.3 outbound shadow ... sticker=skipped stickerReason=below_threshold ...
```

## Typed Media 与 Final Reply 保护

V4.3.1 识别：

- `event.media`；
- `event.payload.media`；
- `mediaUrl / mediaUrls` 兼容字段；
- metadata 中的旧媒体字段。

带媒体的正文不进入多气泡强制拆分。

如果 Hook 提供 `kind`，Coordinator 只消费 `kind=final`；如果提供 role，则只接受 assistant/ai/bot。中间 progress/tool payload 不应提前消费父 Turn 的 Planner Intent。

## Enforced Coordinator

安全情况下：

```text
target=1 -> 显式单气泡 pass-through
target=2/3 -> 自然边界恰好匹配时由 Coordinator 发送
```

以下情况 fail-open：

- 代码块；
- 链接；
- Markdown 列表/结构化内容；
- 文本太短或太长；
- 无法得到目标数量的自然边界；
- 已携带媒体；
- outbound adapter 不可用；
- adapter loader 抛错；
- 第一个子气泡发送前失败。

如果旧 V3 回退返回 `undefined`，Coordinator 显式返回：

```json
{
  "content": "<original assistant text>",
  "cancel": false
}
```

避免宿主把隐式空结果解释为无回复。

如果已经成功发送部分子气泡再失败，则取消父消息并记录 `partial`，避免重复整段发送。

## 实机验收（alpha.4 及之前）

已完成：

- Shadow target/actual 匹配；
- Shadow mismatch；
- Shadow 关闭回退；
- Enforced 单气泡；
- Enforced 精确双气泡；
- 短文本安全回退；
- natural-boundary 回退；
- 编号列表结构化回退；
- explicit parent pass-through；
- Enforced 关闭回退到 V3；
- Sticker `pending → sent`；
- Sticker `skipped`；
- Sticker 单次媒体发送；
- Shadow 关闭后无 V4.3 残留。

## V4.3.1 自动化验收

新增覆盖：

- 同一 session 的两个强 `runId` 同时 pending；
- 交错/逆序完成时 Planner target 与 `stateVersion` 不串轮；
- Sticker delayed terminal 不串到下一 Turn；
- 强 ID miss 不落到另一个强 Turn；
- 缺少早期强 ID 时仅进行无歧义弱别名兼容；
- structured ResponsePlan 不含用户/助手原文；
- typed media 不拆分；
- non-final payload 不消费 Planner Intent；
- `loadAdapter()` 抛错 fail-open；
- partial delivery 防重复；
- Sticker typed `sent/skipped/failed` 终态；
- 新功能全部关闭时旧 V3 行为保持不变。

## 当前已知限制 / 下一阶段

V4.3.1 仍有以下非阻塞项，计划放到后续版本：

1. Sticker 目前仍通过延迟时间保证“文字先、图片后”，尚未改成真正的 Text Delivery Barrier；
2. `actualBubbleCount` 在宿主 pass-through 路径更接近“预期/尝试发送数”，不是所有路径都由 `message_sent` 终态确认；
3. 多气泡仍会直接调用微信 `adapter.sendText()`，需要继续调研 OpenClaw 标准 `reply_dispatch` 多 payload 能力；
4. `stickerPreference` 与 `replyLength` 当前主要用于 Planner 审计，尚未作为强制执行规则；
5. 结构化内容保护仍是保守的 all-or-nothing，未来可引入 MessageSegment/MessageChain 模型。

这些项不会阻塞 V4.3.1 的核心安全加固，但合并前仍需要一次本地实机回归。

## Feature Flags

```json
{
  "unifiedOutboundShadowEnabled": false,
  "unifiedOutboundEnforceEnabled": false
}
```

安装脚本、Schema 和示例配置均显式默认关闭。

## 安全边界

- 不新增第二次聊天模型推理；
- 不新增在线 VLM；
- 不修改 Sticker Catalog、素材和本地阈值；
- Outbound / Planner Store 不保存用户或助手正文；
- Sticker Runtime 原有短期 user message 只用于 V2.1 post-reply 决策，生命周期事件本身不携带正文；
- 所有关联 Store 有 TTL/容量边界；
- 有歧义时宁可不关联，也不跨 Turn 猜测；
- 所有协调异常必须保留原始文本链路；
- PR 在实机回归完成前保持 Draft。