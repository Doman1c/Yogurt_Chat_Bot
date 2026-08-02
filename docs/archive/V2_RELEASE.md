# Yogurt Chat Bot V2.0 发布与验收归档

发布日期：2026-08-02  
状态：稳定  
目标分支：`main`

## 版本组成

```text
Yogurt Chat Bot: V2.0
Yogurt Social Injector: 0.3.3
Yogurt Sticker Tagger: 0.2.0
Catalog Schema: 1.0
```

实机验收环境：

- macOS
- OpenClaw `2026.7.1-2`
- `@tencent-weixin/openclaw-weixin` `2.4.6`
- Ollama
- `qwen3.5:9b`
- 微信官方 Bot 私聊
- 14 张 `approved + enabled` 表情资产

## 发布目标

V2 在保留 V1 社交层能力的基础上，实现：

- 独立离线 Sticker Tagger；
- 正式 Catalog；
- 实时安全候选召回；
- auto 单次聊天模型推理；
- tool 调试模式；
- 冷却与数量限制；
- 不描述图片；
- 微信真实图片发送；
- 完整 fail-open。

## 最终架构

```text
微信消息
→ openclaw-weixin
→ before_prompt_build
→ V1 SocialLayerService + V2 StickerRuntime
→ 本地 auto Top 1
→ 简短回复提示
→ Qwen 单次推理
→ agent_end
→ openclaw-weixin outbound adapter
→ sendMedia
→ 微信 CDN
→ 图片消息
```

`message_sent` 保留为调度回退；同一 pending snapshot 只消费一次。

## 关键技术结论

### 1. 不使用 bundled-only 附件接口

第三方插件不能依赖 `sendSessionAttachment`。该接口可能返回 `{ ok: false }` 而不抛异常，必须检查返回值。

最终实现不再调用它。

### 2. 不以 reply_payload_sending 作为腾讯微信主链路

腾讯 `openclaw-weixin` 当前使用低层兼容 dispatcher。实机中 `reply_payload_sending` 虽已注册，但没有进入该发送路径。

最终改用：

```text
api.runtime.channel.outbound.loadAdapter("openclaw-weixin")
adapter.sendMedia(...)
```

### 3. agent_end 需要会话访问权限

非 bundled 插件必须开启：

```text
plugins.entries.yogurt-social-injector.hooks.allowConversationAccess=true
```

否则 OpenClaw 会阻止 `agent_end`。

### 4. WebP 不是本次故障根因

`openclaw-weixin` 可识别 WebP。最终实机成功发送的资产为 Tagger 标准化 WebP，不需要批量转 PNG。

### 5. 自动模式不注入图片描述

候选描述不进入 Qwen Prompt。模型只收到：

- 系统会在正文后发送图片；
- 只需一句短确认或颜文字；
- 不得描述、解释或虚构画面。

## 安全策略

候选必须满足：

```text
status = approved
enabled = true
content_warnings = ["none"]
relationship 合法
文件存在
路径位于 assets/stickers/
文件大小不超过配置上限
```

明确请求 fallback 只放宽评分门槛，不绕过安全过滤。

## 实机验收用例

### 明确请求

输入：

```text
酸奶，发一个卖萌的表情包过来！
```

预期：

- 候选数大于 0；
- 本地选择一张；
- 文字简短；
- 不描述图片；
- 图片在正文后到达。

结果：通过。

### 禁止发送

输入：

```text
酸奶，不要发表情包，只回复晚安
```

预期：只发送文字。

结果：通过。

### 不同语义匹配

输入：

```text
酸奶，发一个开心庆祝的表情包
```

预期：选择不同语义候选并发送。

结果：通过。

## 成功日志

```text
yogurt-social-injector: injection ready ... stickerSelected=sticker-... stickerDecision=explicit
yogurt-social-injector: sticker outbound scheduled trigger=agent_end ...
outbound: text sent OK ...
yogurt-social-injector: sticker media sent trigger=channel_outbound_adapter ...
```

正文发送日志中的：

```text
mediaUrl=none
```

在 `0.3.3` 是正常的，因为正文和图片是两次独立 outbound 调用。

## 测试与 CI

本地脚本：

```bash
bash scripts/test-openclaw-adapter
bash scripts/test-sticker-explicit-fallback
bash scripts/test-sticker-auto-delivery
```

CI：

- Python 3.9
- Python 3.10
- Python 3.11
- Python 3.12
- Node.js 22

## 已知边界

- 腾讯官方微信 Bot 当前仅私聊；
- 当前 14 张是验收资产数量，不是固定容量；
- 聊天时不运行视觉模型；
- 普通对话不自动写入长期记忆；
- 主动表情受阈值、分差和冷却控制；
- `reply_payload_sending` 仍作为其他 dispatcher 的兼容路径保留。

## 回滚

只关闭 V2：

```bash
openclaw config set \
  'plugins.entries.yogurt-social-injector.config.stickerEnabled' \
  'false' \
  --strict-json
openclaw gateway restart
```

关闭整个插件：

```bash
openclaw plugins disable yogurt-social-injector
openclaw gateway restart
```
