# Yogurt V4.3 Unified Outbound Coordinator

V4.3 解决 V4.1 实机测试中发现的结构性差异：

```text
Response Planner 建议 bubbles=1
V3 multi-bubble 实际发送 count=3
```

问题不在 Planner 或拆分器单独失效，而在于两者分别做决定，缺少同一份可执行的发送计划。

## 目标

在不增加第二次聊天模型推理的前提下，把以下行为收敛到一个可检查、可回滚的 Outbound Plan：

- 回复规划器建议的气泡目标；
- 实际文本拆分结果；
- 表情倾向与最终图片结果；
- 单条、拆分、部分发送和 fail-open 降级；
- 计划值与实际值的差异审计。

## 非目标

V4.3 不负责：

- 生成新的聊天正文；
- 调用第二个 LLM；
- 在线调用 VLM；
- 改写用户长期记忆；
- 保存用户原文或助手完整回复；
- 修改 Sticker Catalog 评分逻辑。

## 数据模型

### OutboundIntent

由 V4.1 `ResponsePlan` 映射而来，只保存白名单字段：

```json
{
  "planVersion": "4.1",
  "bubbleTarget": 1,
  "stickerPreference": "none",
  "replyLength": "very_short",
  "stateVersion": 12,
  "source": "response_planner"
}
```

不保存用户消息、助手回复或 Prompt。

### OutboundResult

描述实际发送结果：

```json
{
  "actualBubbleCount": 3,
  "textChars": 88,
  "outcome": "split",
  "stickerOutcome": "unknown"
}
```

`textChars` 只保存长度，不保存正文。

### OutboundComparison

```json
{
  "bubbleDelta": 2,
  "bubbleTargetMatched": false,
  "reasons": ["actual_bubbles_exceeded_target"]
}
```

## 实施阶段

### V4.3a：模型与一次性 Turn Store

- 建立 `OutboundIntent / OutboundResult / OutboundComparison`；
- 以会话别名保存一次性计划；
- 支持 TTL、容量限制和单次消费；
- 不接管现有发送 Hook；
- 不改变微信回复。

### V4.3b：Shadow Coordinator

- V4 observer 在需要时生成 ResponsePlan；
- 将白名单字段注册为 OutboundIntent；
- V3 multi-bubble 报告实际结果；
- 输出 `targetBubbles / actualBubbles / delta` 日志；
- 仍由 V3 原逻辑决定实际发送。

### V4.3c：Enforced Coordinator

- 统一计划成为实际发送的唯一入口；
- 在安全约束允许时，使实际气泡数等于目标；
- 代码块、长技术回复、媒体消息和发送失败可以安全降级；
- 降级必须记录明确原因；
- Sticker finalizer 与文本发送共享同一 Turn。

## 关键验收

```text
实际发送气泡数 = Outbound Plan 目标
```

允许的例外：

- 文本无法安全拆成目标数量；
- 代码、链接、长技术内容禁止拆分；
- 已携带媒体；
- outbound adapter 不可用；
- 部分发送失败；
- 功能关闭或运行时异常。

所有例外必须 fail-open，并记录结构化原因。

## 安全边界

- 新功能默认关闭；
- Turn Store 只保存枚举、数字和会话别名；
- TTL 默认 15 分钟；
- 每个计划只能消费一次；
- 子气泡不得再次消费父级计划；
- 任何协调失败都保留原始 V3 文本链路；
- 不覆盖本地 Sticker 阈值或运行配置。
