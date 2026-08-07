# Yogurt Chat Bot（酸奶）

酸奶是一个运行在 **OpenClaw + Ollama + 微信官方 Bot** 之上的本地 AI 角色项目。

项目尽量保持聊天主链路轻量：正常聊天只进行一次聊天模型推理，自动记忆、多气泡和在线表情决策不会再调用第二个聊天模型，实时聊天也不会调用视觉模型。

## 当前开发版本

```text
Yogurt Chat Bot: V4.3.1 hardening
OpenClaw plugin: yogurt-social-injector 0.6.3-alpha.5
Plugin entry: integrations/openclaw/index-v43.ts
Sticker Runtime: V2.1
Catalog Schema: 1.0
```

当前 V4.3.1 仍在 Draft PR 中，Feature Flags 默认关闭；在实机回归完成前不应直接开启为默认行为。

## 核心能力

- V1：社交场景、人格与稳定记忆上下文；
- V2.1：最终回复上的表情决策、安全过滤与本地图片发送；
- V3：确定性自动记忆、自然多气泡；
- V4.0：会话状态；
- V4.1：Response Planner；
- V4.2：记忆召回 Shadow Gate；
- V4.3：统一 Outbound Intent / Result、Shadow 与 Enforced Coordinator；
- V4.3.1：强 Turn Correlation、结构化 Planner 交接、类型化 Sticker Lifecycle、typed media / final payload 保护。

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
  │   └── legacy natural multi-bubble
  │
  ├── V4 State / V4.1 Planner / V4.2 Recall Gate
  │   ├── ConversationState
  │   ├── ResponsePlan Prompt Context
  │   └── Structured ResponsePlan Snapshot
  │
  └── V4.3.1 Outbound Coordinator
      ├── strong Turn correlation
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

## 为什么 V4.3.1 需要 Turn Correlation

同一微信会话可能在前一轮 Sticker 还没发送时立刻进入下一轮。如果只用：

```text
session / sender / peer
```

作为单值 key，后一轮会覆盖前一轮。

V4.3.1 优先使用：

```text
runId / messageId / traceId
```

并保留 session/sender/peer/account 作为弱兼容别名。

规则是：

- 不同强 Turn ID 可以共享同一 session 而同时存在；
- 强 ID 精确命中优先；
- 不同强 ID 之间绝不弱回退；
- 只有早期 Hook 没有强 ID、且弱别名只有唯一候选时才兼容回退；
- 一旦存在歧义就放弃关联，而不是猜测另一个 Turn。

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

V4.3.1 不再从中文 Prompt Context 反向正则解析这些字段，而是通过结构化 Store 直接交给 Coordinator。因此 V4.3 日志中的 `stateVersion` 应与 Planner 的真实版本一致，不再固定为 `0`。

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
- Sticker 失败不撤销已生成文本；
- V4.3 adapter loader 抛错时回退原文本；
- typed `event.media` / `payload.media` 不进入强制拆分；
- 如果 Hook 提供 `kind`，只有 `kind=final` 才消费 Planner Intent；
- 子气泡不会再次消费父级计划；
- 所有 Turn Store 都有 TTL 和容量边界；
- 有关联歧义时宁可不协调，也不跨 Turn 猜测。

## 本地文件保护

以下内容可能是本地运行资产，不应在普通代码升级时删除或重置：

```text
assets/stickers/images/
assets/stickers/stickers.json
social_layer/config/stickers.json
本地未提交的阈值调整
```

普通 linked-plugin 升级不需要重新运行安装脚本，也不要执行：

```text
git clean
git reset --hard
```

## 首次安装

首次安装才使用：

```bash
cd ~/Yogurt_Chat_Bot
bash scripts/install-openclaw-adapter
```

安装脚本会：

- 校验插件文件；
- 备份 OpenClaw 配置；
- 以本地路径 link 插件；
- 写入基础运行路径；
- 将高风险/实验功能保持为默认关闭；
- 校验 OpenClaw 配置和插件状态。

## 已安装环境升级

如果插件已经通过本地路径 link 到仓库，普通升级只需要：

```bash
cd ~/Yogurt_Chat_Bot
git pull --ff-only

bash scripts/test-openclaw-adapter
bash scripts/test-sticker-explicit-fallback
bash scripts/test-sticker-auto-delivery
bash scripts/test-sticker-v21
bash scripts/test-v3
bash scripts/test-v3-runtime
bash scripts/test-v3-sticker-bubble-order
bash scripts/test-v4-state
bash scripts/test-v4-runtime
bash scripts/test-v4-outbound
bash scripts/test-v4-outbound-runtime

openclaw config validate
openclaw plugins doctor
openclaw gateway restart
```

如果本地存在未提交修改，应先做 patch/stash 保护，再 fast-forward 拉取并恢复本地修改。

## 当前自动化验证

CI 覆盖 Python 3.9–3.12 和 Node.js 22。

V4.3.1 新增验证包括：

- 两个 run 在同一 session 同时 pending；
- 逆序完成不串 Planner target/stateVersion；
- Sticker delayed terminal 不串轮；
- typed media 不拆分；
- non-final payload 不提前消费 Planner Intent；
- adapter loader exception fail-open；
- partial direct delivery 防重复；
- structured Planner 不携带原始用户/助手文本；
- typed Sticker sent/skipped/failed；
- 两个 V4.3 Flags 都关闭时 legacy V3 保持不变。

## 已知下一阶段工作

V4.3.1 完成的是“合并前安全加固”，尚未解决：

1. 真实 Text Delivery Barrier：Sticker 现在仍主要依赖发送延迟保证文字在前；
2. `message_sent` 级别的 delivered bubble 终态统计；
3. 调研 OpenClaw 标准 `reply_dispatch` 是否能替代直接 adapter 多气泡发送；
4. Planner `stickerPreference / replyLength / askFollowup` 的更完整审计或软约束；
5. 从纯字符串拆分升级到 Text/Code/List/Link/Image Segment 模型；
6. 通用 ConversationState 与当前项目版本词典进一步解耦；
7. 可选的轻量管理/观测页面。

这些工作应在 V4.3.1 实机回归稳定后继续，而不是一次性叠加到同一验证批次。

## 设计文档

- `docs/V1_DESIGN.md`
- `docs/V2_DESIGN.md`
- `docs/V2_1_DESIGN.md`
- `docs/V3_RESEARCH_PLAN.md`
- `docs/V4_3_DESIGN.md`
