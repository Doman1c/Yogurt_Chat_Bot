# Yogurt Social Layer V2 表情包系统设计

> 状态：完成并通过微信实机验收  
> 版本：Yogurt Chat Bot V2.0 / Plugin 0.3.3  
> 日期：2026-08-02  
> 基线：V1.0 社交层

## 1. 目标

V2 在不破坏 V1、不过度增加本地模型负担的前提下，实现：

- 离线图片分类与人工审核；
- 运行时安全候选召回；
- 默认单次 Qwen 推理；
- 可控主动表情；
- 明确请求可靠发送；
- 不描述或虚构图片；
- 微信真实图片消息；
- fail-open 和可回滚。

## 2. 两仓库架构

### Yogurt Sticker Tagger

独立仓库：`Doman1c/Yogurt_Sticker_Tagger`

职责：

```text
原始图片
→ 校验与 SHA-256 去重
→ WebP 归一化和缩略图
→ Ollama 视觉分类
→ 人工审核和受约束编辑
→ SQLite 审计
→ 原子导出 Catalog
```

不连接微信，不参与聊天实时推理。

### Yogurt Chat Bot

职责：

```text
用户消息
→ V1 场景、关系与显式记忆
→ V2 Catalog 候选召回
→ 本地选择策略
→ Qwen 文本回复
→ OpenClaw 微信媒体发送
```

Chat Bot 只读 Tagger 的正式 Catalog，不访问 Tagger SQLite。

## 3. 唯一 Catalog 契约

权威 Schema：

```text
Yogurt_Sticker_Tagger/schemas/sticker-catalog-v1.schema.json
```

版本：

```json
{
  "schema_version": "1.0"
}
```

正式候选至少依赖：

```text
id
file
status
enabled
description
scenes
emotions
tones
keywords
relationships
content_warnings
weight
cooldown_seconds
```

Chat Bot 不维护第二套图片 Schema。

## 4. 安全资格过滤

候选必须满足：

```text
status = approved
enabled = true
content_warnings = ["none"]
relationship 合法
文件存在
路径位于 assets/stickers/
```

`content_warnings` 采用严格 fail-closed：只有精确的 `["none"]` 表示可安全发送。

## 5. 召回评分

基础信号：

- scene：4 分；
- mood：3 分；
- keyword：每个 2 分，最多两个；
- relationship：1 分；
- 乘以资产 `weight`。

普通候选低于 `minimum_score` 时不返回。

### 明确请求

明确要求表情时：

- 传入 `explicit_sticker_request=true`；
- 评分增加明确意图权重；
- 若仍无普通候选，从全部安全合格资产中执行相关性 fallback；
- fallback 只放宽评分，不绕过安全过滤。

## 6. 两种选择模式

### auto（默认）

```text
Retriever Top-K
→ TypeScript 本地 Top 1
→ 不向模型公开 choose_sticker
→ Qwen 单次推理
```

优点：

- 单次模型调用；
- 延迟和上下文更低；
- 不依赖 9B 模型稳定调用工具；
- 选择可解释且易测试。

### tool（调试）

```text
候选注入
→ Qwen 调用 choose_sticker
→ ID 校验
→ 第二次推理生成正文
```

只用于验证模型选择能力。

## 7. 自动策略

### 明确请求

```text
Top 1 >= explicit_min_score
```

不受主动发送冷却阻止，但仍受安全和文件校验限制。

### 主动发送

```text
Top 1 >= proactive_min_score
Top 1 - Top 2 >= proactive_min_margin
```

并执行：

- 全局冷却；
- Scene 冷却；
- 单图冷却；
- 每小时数量上限。

用户明确禁止表情时，本轮强制不发送。

## 8. Prompt 设计

自动模式不注入具体候选描述。

成功选择时只注入：

```text
系统会在正文后发送一张表情。
正文只需要一句很短的确认或颜文字。
不得描述、解释或复述图片画面。
不得提及 ID、文件、路径、候选或系统流程。
```

无候选时：

```text
只简短说明本次没有发出来。
不得虚构或描述任何图片。
```

## 9. Pending snapshot

每轮候选注册为短期 snapshot：

- 绑定 session、sender、conversation 和 peer 别名；
- 有 TTL；
- 只允许选择候选内稳定 ID；
- 同 ID 重复选择幂等；
- 每轮最多一张；
- 消费后删除 snapshot；
- `agent_end` 与 `message_sent` 同时出现也不会重复发送。

## 10. 微信发送实现

### 曾评估但未采用的主路径

#### sendSessionAttachment

第三方插件不能依赖 bundled-only `sendSessionAttachment`。失败可能通过 `{ ok: false }` 返回而不抛异常。

#### reply_payload_sending

OpenClaw 提供该 Hook，但腾讯 `openclaw-weixin` 当前实机使用低层兼容 dispatcher，没有稳定触发第三方 `reply_payload_sending`。

### 最终实机路径

```text
agent_end
→ 等待正文落地
→ api.runtime.channel.outbound.loadAdapter("openclaw-weixin")
→ adapter.sendMedia(...)
→ openclaw-weixin 读取本地文件
→ 微信 CDN 上传
→ 微信图片消息
```

`message_sent` 作为兼容回退，会调整同一 snapshot 的发送定时器。

## 11. Hook 权限

必需：

```json
{
  "allowConversationAccess": true,
  "allowPromptInjection": true,
  "timeouts": {
    "before_prompt_build": 12000
  }
}
```

非 bundled 插件缺少 `allowConversationAccess` 时，`agent_end` 会被 OpenClaw blocked。

## 12. 文件安全

发送前：

1. 从 Catalog 候选取得相对路径；
2. 以 `assets/stickers/` 为根；
3. 解析 `realpath`；
4. 拒绝目录逃逸；
5. 确认普通文件；
6. 检查大小；
7. 调用频道 adapter；
8. 只有成功后记录冷却。

模型不能提供或修改本地路径。

## 13. Fail-open

以下失败不阻断文本：

- Python 启动失败；
- bridge 超时或非法 JSON；
- SQLite 失败；
- Catalog 不存在或无候选；
- 图片路径非法；
- outbound adapter 不可用；
- 微信上传失败。

## 14. 测试

```bash
bash scripts/test-openclaw-adapter
bash scripts/test-sticker-explicit-fallback
bash scripts/test-sticker-auto-delivery
```

覆盖：

- V1 原始消息保留和 fail-open；
- Catalog 加载和严格安全过滤；
- 明确请求参数；
- 低权重 fallback；
- 无候选不描述图片；
- auto 隐藏工具；
- tool ID 校验；
- agent_end / message_sent 去重；
- outbound adapter 和 sendMedia；
- 发送成功后冷却。

CI：Python 3.9～3.12、Node.js 22。

## 15. 实机验收

环境：

```text
OpenClaw 2026.7.1-2
openclaw-weixin 2.4.6
qwen3.5:9b
微信官方 Bot 私聊
14 张 approved + enabled 表情
```

验收通过：

- 明确请求发送真实图片；
- 禁止请求只发文字；
- 不同语义选择不同候选；
- 正文简短且不描述图片；
- WebP 图片成功显示。

## 16. 已知边界

- 官方微信 Bot 当前仅私聊；
- 14 是当前资产基线，不是固定上限；
- 聊天时不运行视觉模型；
- 普通聊天不自动写长期记忆；
- 主动表情受阈值和冷却限制；
- `reply_payload_sending` 保留为其他 dispatcher 的兼容路径。
