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

描述同一父级 Turn 的实际发送结果：

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

- `pending`：已选中并安排发送，等待媒体适配器结果；
- `sent`：媒体适配器确认发送；
- `skipped`：Finalizer 或策略明确决定不发；
- `failed`：已经进入选择/发送链路，但最终失败；
- `unknown`：仅作为内部构造阶段占位，不应出现在最终 V4.3 日志中。

`textChars` 只保存长度，不保存正文；`stickerReason` 是经过截断和字符白名单处理的有限原因码。

### OutboundComparison

```json
{
  "bubbleDelta": 0,
  "bubbleTargetMatched": true,
  "reasons": ["bubble_target_matched"]
}
```

Sticker 终态会额外附加 `sticker_pending`、`sticker_skipped` 或 `sticker_failed`。

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
- 当旧 V3 对降级消息没有返回修改或取消结果时，Coordinator 显式返回 `{ content, cancel: false }`，确保宿主继续投递原父消息；
- 日志增加 `hostAction=explicit_passthrough|legacy_result|coordinator_delivery`，用于实机确认最终投递路径。

### V4.3d：Sticker Finalizer 终态关联

- 不改 V2.1 的候选、评分、阈值、选图或媒体发送代码；
- V4.3 外层捕获现有的 `selected / scheduled / sent / rejected / failed` 有限日志；
- 使用 `AsyncLocalStorage` 保留 Finalizer 定时器所属父 Turn 的 aliases；
- 把 Sticker 状态写回同一个父级 `OutboundResult`；
- 已安排发送时先记录 `sticker=pending`；
- 媒体发送完成后追加一条 `v4.3 outbound finalized ... finalStage=sticker`；
- Finalizer 明确不发时，父级结果直接记录 `sticker=skipped`；
- 媒体适配器、文件或决策异常记录 `sticker=failed` 与有限原因码；
- 45 秒仍未获得已安排媒体的终态时，记录 `delivery_outcome_timeout`；
- 所有关联 Store 均为 TTL/容量有界，且不保存用户或助手正文。

示例：

```text
yogurt-social-injector: v4.3 outbound enforced ... sticker=pending stickerReason=delivery_scheduled ...
yogurt-social-injector: v4.3 outbound finalized mode=enforced ... sticker=sent stickerReason=media_sent ... finalStage=sticker
```

明确跳过：

```text
yogurt-social-injector: v4.3 outbound enforced ... sticker=skipped stickerReason=below_threshold ...
```

明确失败：

```text
yogurt-social-injector: v4.3 outbound finalized mode=enforced ... sticker=failed stickerReason=weixin_send_media_unavailable ... finalStage=sticker
```

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

所有例外必须 fail-open，并记录结构化原因。对于单条原文回退，日志还必须显示 `hostAction=explicit_passthrough`，且微信端收到完整原文而不是空响应提示。

Sticker 验收要求：

- V4.3 父级日志不再以 `sticker=unknown` 作为最终结果；
- 成功发送必须出现 `sticker=sent`；
- 明确不发必须出现 `sticker=skipped`；
- 失败必须出现 `sticker=failed` 和 `stickerReason`；
- 子气泡和媒体子发送不得创建第二个父级 OutboundResult。

## 安全边界

- 新功能默认关闭；
- Turn Store 只保存枚举、数字、有限原因码和会话别名；
- TTL 默认 15 分钟；
- 每个计划只能消费一次；
- 子气泡不得再次消费父级计划；
- 任何协调失败都保留原始 V3 文本链路；
- 不覆盖本地 Sticker 阈值、素材或运行配置。
