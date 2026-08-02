# Yogurt Social Injector（OpenClaw 混合插件）

这是 Yogurt Chat Bot 的原生 OpenClaw 插件，当前同时提供：

- V1 `before_prompt_build` 社交上下文注入；
- V2 表情包候选召回；
- 默认 `auto` 单次推理选图；
- 可选 `tool` 双次推理调试模式；
- 通过 OpenClaw 正常回复媒体链路发送实际表情图片。

当前插件版本：`0.3.1`。

## 默认流程：`auto`

```text
微信消息
  ↓
before_prompt_build
  ↓
Python bridge
  ├── V1 SocialLayerService
  └── V2 StickerRuntime 候选召回与评分
  ↓
TypeScript 自动选择策略
  ├── 用户明确要求表情：较宽松门槛
  ├── 主动发送：较高门槛与分差要求
  ├── 全局 / Scene / 单图冷却
  └── 每小时数量限制
  ↓
只向 Qwen 注入“简短确认、不要描述画面”的提示
  ↓
Qwen 只推理一次并生成正文
  ↓
reply_payload_sending
  ↓
将选中表情的绝对路径写入最终 ReplyPayload.mediaUrls
  ↓
OpenClaw 正常 outbound dispatcher
  ↓
openclaw-weixin 上传微信 CDN 并发送图片消息
```

`auto` 模式不会向模型暴露 `choose_sticker`，因此不会因为工具调用触发第二次 Qwen 推理，也不会把具体表情描述暴露给正文生成。

## 调试流程：`tool`

设置：

```json
{
  "stickerSelectionMode": "tool"
}
```

流程：

```text
候选列表注入 Prompt
  ↓
Qwen 调用 choose_sticker(sticker_id)
  ↓
插件校验 ID 属于本轮候选
  ↓
Qwen 第二次推理生成最终正文
  ↓
reply_payload_sending 将所选图片附到最终回复
```

这个模式用于验证模型选图能力，不建议作为本地 9B 模型的日常默认模式。

## 自动选择策略

用户明确要求表情时：

```text
Top 1 score >= explicit_min_score
```

默认：

```json
{
  "explicit_min_score": 5.0
}
```

酸奶主动使用表情时：

```text
Top 1 score >= proactive_min_score
并且
Top 1 - Top 2 >= proactive_min_margin
```

默认：

```json
{
  "proactive_min_score": 7.0,
  "proactive_min_margin": 2.0
}
```

主动发送还会执行：

- 全局冷却；
- Scene 冷却；
- 单张表情冷却；
- 每小时数量限制。

用户明确说“不要发 / 别发表情包”时，本轮不会自动发送。

## 图片发送与安全边界

候选 Catalog 中只允许相对路径，例如：

```text
images/sticker-0123456789ab.webp
```

附加到回复前会：

1. 解析到 `assets/stickers/`；
2. 解析真实文件路径；
3. 拒绝逃出表情目录的路径；
4. 确认目标是普通文件；
5. 只在 `kind=final` 的 `reply_payload_sending` Hook 中加入 `mediaUrls`；
6. 交给当前频道的正常 `sendMedia` 实现，而不是调用 bundled-only 的 `sendSessionAttachment`。

对 `openclaw-weixin`，成功的真实链路应出现：

```text
outbound: ... mediaUrl=present
[weixin] sendWeixinMediaFile: uploading image ...
[weixin] sendWeixinMediaFile: image upload done ...
sendImageMessageWeixin: success ...
```

插件自己的 `sticker media attached` 只表示图片已进入 OpenClaw 最终回复载荷；是否真正送达以频道上传和发送日志为准。

## 回复载荷行为

表情路径和模型正文进入同一个最终 `ReplyPayload`。OpenClaw 会把正文作为首张媒体的文字部分，并由频道统一执行发送，因此不再需要等待 `agent_end` 后额外创建第二条附件任务，也不会出现第三方插件调用 `sendSessionAttachment` 后返回 `ok=false` 却误报成功的问题。

`delivery_delay_ms` 暂时保留在 Sticker Runtime 配置中用于旧配置兼容，`0.3.1` 的直接回复媒体链路不再依赖该延迟。

## OpenClaw 插件配置

```json
{
  "stickerEnabled": true,
  "stickerConfigPath": "/Users/your-name/Yogurt_Chat_Bot/social_layer/config/stickers.json",
  "stickerSelectionMode": "auto"
}
```

主要字段：

- `enabled`
- `pythonPath`
- `repoPath`
- `dbPath`
- `defaultUserId`
- `senderMap`
- `ownerSenderIds`
- `unknownSenderPolicy`
- `allowedChannels`
- `onlyUserTurns`
- `timeoutMs`
- `maxMemories`
- `stickerEnabled`
- `stickerConfigPath`
- `stickerSelectionMode`

完整示例见 `integrations/openclaw/config.example.json`。

## Sticker Runtime 配置

```text
social_layer/config/stickers.json
```

主要字段：

```json
{
  "enabled": true,
  "catalog_path": "assets/stickers/stickers.json",
  "minimum_score": 5.0,
  "max_candidates": 5,
  "require_files": true,
  "explicit_min_score": 5.0,
  "proactive_min_score": 7.0,
  "proactive_min_margin": 2.0,
  "delivery_delay_ms": 1500,
  "delivery_max_bytes": 10485760,
  "global_cooldown_seconds": 300,
  "scene_cooldown_seconds": 900,
  "default_sticker_cooldown_seconds": 1800,
  "max_stickers_per_hour": 4,
  "allowed_channels": ["ilink"]
}
```

缺少新字段时会使用安全默认值，因此旧的本地 `stickers.json` 不需要立即重建。

## 隐藏微信工具进度卡片

腾讯微信插件默认可能显示 `Choose Sticker`、`Web Fetch` 等工具进度卡片。关闭方式：

```bash
openclaw config set \
  'channels.openclaw-weixin.replyProgressMessages' \
  'false' \
  --strict-json
```

`auto` 模式本身不会调用 `choose_sticker`；关闭该频道设置还能隐藏其他工具的进度卡片。

## 日志

自动模式成功选择：

```text
yogurt-social-injector: injection ready ... stickerMode=auto stickerCandidates=... stickerSelected=... stickerDecision=explicit|proactive
```

成功附加到最终回复：

```text
yogurt-social-injector: sticker media attached trigger=reply_payload_sending mode=auto sticker=... mediaCount=1
```

未发送的常见原因：

```text
no_candidates
explicit_threshold
proactive_threshold
score_margin
global_cooldown
candidate_cooldown
hourly_limit
user_forbid
```

路径校验失败时：

```text
yogurt-social-injector: sticker media attach failed (...); text preserved
```

## 安装与更新

```bash
bash scripts/install-openclaw-adapter
```

安装脚本会验证：

- Python 社交层与 fail-open；
- Sticker Catalog 与 Retriever；
- `index-v2.ts` 插件入口；
- `sticker-plugin-utils.ts`；
- `sticker-selection.ts`；
- `auto` 模式隐藏工具；
- `tool` 模式候选 ID 校验；
- `reply_payload_sending` 最终媒体附加与一次性消费。

## 验证

```bash
openclaw config validate
openclaw plugins doctor
openclaw plugins inspect yogurt-social-injector --runtime --json
openclaw gateway restart
openclaw logs --follow --local-time
```

## 核心保证

- 不修改 `event.prompt`；
- 用户原始消息逐字保留；
- `auto` 模式只调用一次聊天模型；
- 不向模型注入具体表情画面描述；
- 不在聊天时调用视觉模型；
- 不从普通聊天自动写入长期记忆；
- 只允许 Catalog 中通过目录边界校验的本地图片；
- Python、SQLite、Catalog 或图片附加故障时 fail-open；
- V2 关闭时维持 V1 原行为；
- 不把消息正文、Token 或绝对图片路径写入常规日志。

## 关闭与回滚

只关闭 V2、保留 V1：

```bash
openclaw config set \
  'plugins.entries.yogurt-social-injector.config.stickerEnabled' \
  'false' \
  --strict-json
```

切回双次推理调试模式：

```bash
openclaw config set \
  'plugins.entries.yogurt-social-injector.config.stickerSelectionMode' \
  '"tool"' \
  --strict-json
```

恢复单次推理：

```bash
openclaw config set \
  'plugins.entries.yogurt-social-injector.config.stickerSelectionMode' \
  '"auto"' \
  --strict-json
```

关闭整个插件：

```bash
openclaw plugins disable yogurt-social-injector
openclaw gateway restart
```
