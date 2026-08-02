# Yogurt Social Injector（OpenClaw 插件）

这是 Yogurt Chat Bot 的原生 OpenClaw 插件，当前版本：`0.3.3`。

它同时提供：

- V1 `before_prompt_build` 社交上下文注入；
- V2 表情包候选召回；
- 默认 `auto` 单次推理选图；
- 可选 `tool` 双次推理调试模式；
- 通过 `openclaw-weixin` outbound adapter 发送真实图片。

## 已验证的微信发送链路

腾讯 `openclaw-weixin` 当前使用低层兼容 dispatcher，实机中不会稳定触发第三方插件的 `reply_payload_sending`。因此 `0.3.3` 的主发送链路为：

```text
微信消息
  ↓
before_prompt_build
  ↓
Python bridge
  ├── V1 SocialLayerService
  └── V2 StickerRuntime 候选召回
  ↓
TypeScript 自动选择 Top 1
  ↓
只向 Qwen 注入“简短确认、不要描述图片”的提示
  ↓
Qwen 单次推理生成正文
  ↓
agent_end
  ↓
延迟等待正文发送
  ↓
api.runtime.channel.outbound.loadAdapter("openclaw-weixin")
  ↓
adapter.sendMedia({ mediaUrl: absolutePath })
  ↓
微信 CDN 上传
  ↓
微信图片消息
```

`message_sent` 作为兼容回退，会重置同一张 pending 表情的定时器；snapshot 只消费一次，因此不会重复发送。

`reply_payload_sending` 仍保留，用于兼容会正确安装该 Hook 的其他 OpenClaw dispatcher，但不是腾讯微信实机验收的主路径。

## 关键权限

非 bundled 插件要使用 `agent_end`，必须开启：

```json
{
  "hooks": {
    "allowConversationAccess": true,
    "allowPromptInjection": true,
    "timeouts": {
      "before_prompt_build": 12000
    }
  }
}
```

缺少 `allowConversationAccess` 时，`openclaw plugins inspect --runtime --json` 会显示：

```text
typed hook "agent_end" blocked because non-bundled plugins must set
plugins.entries.yogurt-social-injector.hooks.allowConversationAccess=true
```

修复：

```bash
openclaw config set \
  'plugins.entries.yogurt-social-injector.hooks.allowConversationAccess' \
  'true' \
  --strict-json

openclaw config set \
  'plugins.entries.yogurt-social-injector.hooks.allowPromptInjection' \
  'true' \
  --strict-json
```

## 默认流程：`auto`

`auto` 模式不会向模型暴露 `choose_sticker`。

选择策略：

```text
明确请求：Top 1 >= explicit_min_score
主动发送：Top 1 >= proactive_min_score
        且 Top 1 - Top 2 >= proactive_min_margin
```

默认：

```json
{
  "explicit_min_score": 5.0,
  "proactive_min_score": 7.0,
  "proactive_min_margin": 2.0
}
```

明确请求无普通候选时会启用安全 fallback，但仍严格要求：

```text
approved
enabled
content_warnings = ["none"]
relationship 合法
文件存在
```

主动发送还受以下限制：

- 全局冷却；
- Scene 冷却；
- 单图冷却；
- 每小时数量上限。

用户明确说“不要发 / 别发表情包”时，本轮不发送。

## 调试流程：`tool`

```bash
openclaw config set \
  'plugins.entries.yogurt-social-injector.config.stickerSelectionMode' \
  '"tool"' \
  --strict-json
```

流程：

```text
候选列表注入 Prompt
  ↓
Qwen 调用 choose_sticker(sticker_id)
  ↓
插件校验 ID 属于本轮候选
  ↓
Qwen 第二次推理生成正文
  ↓
agent_end / message_sent
  ↓
微信 outbound adapter 发送图片
```

该模式用于调试，不建议作为本地 9B 模型的日常默认模式。

恢复 auto：

```bash
openclaw config set \
  'plugins.entries.yogurt-social-injector.config.stickerSelectionMode' \
  '"auto"' \
  --strict-json
```

## 不向模型描述图片

成功选图时，插件只注入：

- 系统会在正文后发送一张表情；
- 正文只需一句短确认或颜文字；
- 不要描述、解释或复述画面；
- 不要输出 ID、文件名、路径、候选或系统流程。

候选失败时也禁止模型虚构图片内容，只能简短说明本次未发送。

具体候选 `description` 不进入自动模式 Prompt。

## 图片路径与安全边界

Catalog 只允许相对路径，例如：

```text
images/sticker-0123456789ab.webp
```

发送前会：

1. 解析到 `assets/stickers/`；
2. 使用 `realpath` 解析真实路径；
3. 拒绝逃出资产根目录；
4. 确认目标为普通文件；
5. 检查大小不超过 `delivery_max_bytes`；
6. 只发送本轮候选中选中的稳定 ID；
7. 只有 `sendMedia` 成功后才记录冷却。

图片失败不会影响已经发送的正文。

## OpenClaw 插件配置

```json
{
  "plugins": {
    "entries": {
      "yogurt-social-injector": {
        "enabled": true,
        "hooks": {
          "allowConversationAccess": true,
          "allowPromptInjection": true,
          "timeouts": {
            "before_prompt_build": 12000
          }
        },
        "config": {
          "enabled": true,
          "pythonPath": "/usr/bin/python3",
          "repoPath": "/Users/your-name/Yogurt_Chat_Bot",
          "dbPath": "/Users/your-name/.openclaw/yogurt-social/memory.sqlite3",
          "defaultUserId": "owner",
          "senderMap": {},
          "ownerSenderIds": [],
          "unknownSenderPolicy": "default",
          "allowedChannels": [],
          "onlyUserTurns": true,
          "timeoutMs": 2500,
          "maxMemories": 3,
          "stickerEnabled": true,
          "stickerConfigPath": "/Users/your-name/Yogurt_Chat_Bot/social_layer/config/stickers.json",
          "stickerSelectionMode": "auto"
        }
      }
    }
  }
}
```

完整示例见 `config.example.json`。

## Sticker Runtime 配置

位置：

```text
social_layer/config/stickers.json
```

示例：

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
  "max_stickers_per_hour": 4
}
```

`delivery_delay_ms` 用于正文之后的独立图片发送。`agent_end` 默认至少等待 2500 ms，可通过环境变量调试：

```bash
export YOGURT_STICKER_AGENT_END_DELAY_MS=2500
```

## 安装与升级

首次安装：

```bash
bash scripts/install-openclaw-adapter
```

安装脚本默认把 `stickerEnabled` 写为 `false`，完成 Catalog 同步和验证后再开启。

已经使用 `--link` 安装时，升级只需：

```bash
cd ~/Yogurt_Chat_Bot
git pull --ff-only

bash scripts/test-openclaw-adapter
bash scripts/test-sticker-explicit-fallback
bash scripts/test-sticker-auto-delivery

openclaw config validate
openclaw plugins doctor
openclaw gateway restart
```

不要仅为了更新代码重复运行完整安装脚本。

## Runtime 验证

```bash
openclaw plugins inspect \
  yogurt-social-injector \
  --runtime \
  --json
```

应看到：

```text
version: 0.3.3
hookCount: 4
before_prompt_build
agent_end
message_sent
reply_payload_sending
```

允许保留的兼容 Hook：

```text
reply_payload_sending
```

但微信实机成功的关键是 `agent_end` 未被 blocked。

## 日志

选中候选：

```text
yogurt-social-injector: injection ready ...
stickerMode=auto
stickerCandidates=5
stickerSelected=sticker-...
stickerDecision=explicit|proactive
stickerExplicit=true|false
```

安排发送：

```text
yogurt-social-injector: sticker outbound scheduled trigger=agent_end mode=auto hasTarget=true delayMs=2500
```

成功：

```text
yogurt-social-injector: sticker media sent trigger=channel_outbound_adapter mode=auto sticker=sticker-...
```

正文通道仍可能显示：

```text
outbound: ... mediaUrl=none
```

这是正常的，因为正文与图片是两次独立发送。

失败：

```text
sticker outbound delivery skipped (target_missing)
sticker media send failed trigger=channel_outbound_adapter (channel_outbound_adapter_unavailable)
sticker media send failed trigger=channel_outbound_adapter (weixin_send_media_unavailable)
sticker media send failed trigger=channel_outbound_adapter (sticker_file_too_large)
```

未选择的常见原因：

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

## 测试

```bash
bash scripts/test-openclaw-adapter
bash scripts/test-sticker-explicit-fallback
bash scripts/test-sticker-auto-delivery
```

覆盖：

- Python bridge 与 V1 fail-open；
- 明确请求参数传递；
- 低权重安全 fallback；
- 无候选时禁止描述图片；
- auto 模式不注册可见工具；
- agent_end / message_sent 去重；
- outbound adapter 加载；
- `sendMedia` 参数；
- 文件大小和路径校验；
- 发送成功后才提交冷却。

## 隐藏微信进度消息

```bash
openclaw config set \
  'channels.openclaw-weixin.replyProgressMessages' \
  'false' \
  --strict-json
```

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

## 核心保证

- 不修改用户原始消息；
- auto 模式只调用一次聊天模型；
- 不向模型注入表情描述；
- 聊天时不调用视觉模型；
- 不从普通聊天自动写长期记忆；
- 模型不能指定任意本地文件；
- Python、SQLite、Catalog、图片或微信发送异常时 fail-open；
- 正常日志不记录消息正文、Token、图片内容或绝对路径。
