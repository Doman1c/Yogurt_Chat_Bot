# Yogurt Social Injector（OpenClaw 插件）

这是 Yogurt Chat Bot 的原生 OpenClaw 插件，当前稳定版本：`0.4.0`。

它同时提供：

- V1 `before_prompt_build` 社交上下文注入；
- V2.1 发送决策与语义优先表情召回；
- 默认 `auto` 单次聊天推理模式；
- 可选 `tool` 调试模式；
- 通过 `openclaw-weixin` outbound adapter 发送真实图片。

## V2.1 主发送链路

腾讯 `openclaw-weixin` 当前使用低层兼容 dispatcher，第三方插件的 `reply_payload_sending` 不一定稳定触发。因此 0.4.0 的主链路为：

```text
微信消息
  ↓
before_prompt_build
  ↓
Python bridge
  ├── V1 SocialLayerService
  └── V2.1 Sticker Runtime 候选召回
  ↓
Qwen 单次推理生成最终正文
  ↓
agent_end
  ├── 读取最终回复
  ├── StickerSendDecider 判断是否发送
  └── Ranker 选择最相关图片
  ↓
TypeScript 阈值、分差、冷却和每小时上限
  ↓
api.runtime.channel.outbound.loadAdapter("openclaw-weixin")
  ↓
adapter.sendMedia({ mediaUrl: absolutePath })
  ↓
微信 CDN
  ↓
微信图片消息
```

`message_sent` 是兼容回退。`reply_payload_sending` 仍注册，用于兼容其他 dispatcher，但不是腾讯微信实机主路径。

## 关键权限

非 bundled 插件使用 `agent_end` 必须开启：

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

修复命令：

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

`auto` 模式不会向模型暴露 `choose_sticker`，也不会触发第二次 Qwen 推理。

V2.1 将两项决策分离：

```text
Send Decider
→ 这一轮是否适合发表情

Semantic Ranker
→ 如果发送，哪张最相关
```

主动发送会结合：

- 用户消息；
- 酸奶最终回复；
- Scene 和 Mood；
- Top1 质量和 Top1/Top2 分差；
- 技术、严肃和高风险标记；
- 冷却与每小时上限。

通道策略：

```text
celebration 允许
playful    允许
comfort    允许但阈值更高
casual     允许但更克制
technical  主动发送阻断
serious    主动发送阻断
```

高风险求助在任何 Scene 和显式请求模式下都不发表情。

## Ranker 优先级

```text
精确关键词和短语
> Emotion
> Description / OCR
> Tone
> Scene
> Relationship
> 受限 Weight prior
```

Scene 只作为上下文修正，不能再压过“无语、疲惫、拥抱、惊讶”等具体语义。

## 显式请求

```text
explicit_specific
例如：发一个无语的表情
→ 必须达到最低相关性
→ 没有合适图片时不强行发送

explicit_generic
例如：随便发个表情包
→ 从当前聊天的安全候选中选择
```

所有模式都严格要求：

```text
status = approved
enabled = true
content_warnings = ["none"]
relationship 合法
文件存在且路径安全
```

## 调试流程：`tool`

```bash
openclaw config set \
  'plugins.entries.yogurt-social-injector.config.stickerSelectionMode' \
  '"tool"' \
  --strict-json
```

该模式用于验证 `choose_sticker(sticker_id)`，可能触发额外推理，不建议作为本地 9B 模型的日常默认模式。

恢复：

```bash
openclaw config set \
  'plugins.entries.yogurt-social-injector.config.stickerSelectionMode' \
  '"auto"' \
  --strict-json
```

## 不向模型描述图片

插件不会把候选绝对路径或完整图片描述交给模型。正文不能描述、解释、复述或虚构即将发送的图片画面。

## 图片安全边界

发送前会：

1. 只接受本轮候选的稳定 Sticker ID；
2. 解析 `assets/stickers/` 下的相对路径；
3. 使用 `realpath` 拒绝目录逃逸；
4. 确认目标为普通文件；
5. 检查大小上限；
6. 只有 `sendMedia` 成功后才记录冷却。

图片失败不会影响已经发送的正文。

## OpenClaw 配置

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

生产模板见 `social_layer/config/stickers.example.json`。它控制：

- Catalog 路径；
- 最低相关性；
- Top-K；
- 图片文件要求；
- 主动发送阈值和分差；
- 全局、Scene、单图冷却；
- 每小时上限；
- 发送延迟和文件大小上限。

## 安装与升级

首次安装：

```bash
bash scripts/install-openclaw-adapter
```

安装脚本默认把 `stickerEnabled` 设为 `false`。Catalog 部署和测试完成后再开启。

已使用 `--link` 安装时，普通升级：

```bash
cd ~/Yogurt_Chat_Bot
git switch main
git pull --ff-only origin main

bash scripts/test-openclaw-adapter
bash scripts/test-sticker-explicit-fallback
bash scripts/test-sticker-v21
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
version: 0.4.0
status: loaded
hookCount: 4
before_prompt_build
agent_end
message_sent
reply_payload_sending
```

## 日志

V2.1 决策：

```text
send decision
shouldSend=
score=
threshold=
top=
runnerUp=
```

选中和安排发送：

```text
post-reply sticker selected
sticker outbound scheduled trigger=agent_end
```

成功：

```text
sticker media sent trigger=channel_outbound_adapter
```

失败：

```text
sticker media send failed trigger=channel_outbound_adapter (...)
```

正文通道显示 `mediaUrl=none` 是正常的，因为正文与图片是两次独立发送。

## 测试

```bash
bash scripts/test-openclaw-adapter
bash scripts/test-sticker-explicit-fallback
bash scripts/test-sticker-v21
bash scripts/test-sticker-auto-delivery
```

测试覆盖：

- V1 bridge 与 fail-open；
- 语义优先 Ranker；
- specific / generic 显式请求；
- comfort、casual、technical、serious；
- 高风险硬阻断；
- auto 模式隐藏工具；
- agent_end / message_sent 去重；
- outbound adapter；
- 路径和大小校验；
- 发送成功后才记录冷却。

## 回滚

只关闭表情系统：

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

Catalog 回滚由 Yogurt Sticker Tagger 0.3.0 Web Admin 执行。

## 核心保证

- 不修改用户原始消息；
- `auto` 只有一次聊天模型推理；
- 聊天时不调用 VLM；
- 模型不能指定任意本地文件；
- 只发送 Catalog 安全资产；
- 高风险求助不发表情；
- 不描述或虚构图片；
- 发送失败不阻断文字；
- 正常日志不记录消息正文、Token、图片内容或数据库内容。
