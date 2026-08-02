# Yogurt Chat Bot（酸奶）

酸奶是一个运行在 **OpenClaw + Ollama + 微信官方 Bot** 之上的本地 AI 角色项目。

它在不增加第二次聊天模型推理的前提下，为每轮消息补充可解释、可回滚的社交上下文，并根据场景、用户关系和离线审核过的表情包资产，生成自然的文字回复并按需发送真实图片。

## 当前稳定状态

- **V1 社交层**：2026-07-31 完成微信实机验收；
- **V2 表情包系统**：2026-08-02 完成微信实机验收；
- **OpenClaw 插件版本**：`0.3.3`；
- **Sticker Tagger 版本**：`0.2.0`；
- **当前已审核资产基线**：14 张 `approved + enabled` 表情包。

最终实机环境：

- macOS
- OpenClaw `2026.7.1-2`
- `@tencent-weixin/openclaw-weixin` `2.4.6`
- Ollama
- `qwen3.5:9b`
- 微信官方 Bot 私聊

V2 已验证：

- 用户明确要求时会发送实际图片；
- 用户明确禁止时只发送文字；
- 模型正文保持简短，不描述或虚构图片画面；
- 表情包从本地审核 Catalog 中安全召回；
- 图片通过 `openclaw-weixin` outbound adapter、微信 CDN 和原生图片消息链路送达；
- 正文、候选召回或图片发送失败时保持 fail-open，不阻断原聊天。

## 完整架构

```text
微信私聊消息
  ↓
@tencent-weixin/openclaw-weixin
  ↓
OpenClaw before_prompt_build
  ↓
Yogurt Social Injector 0.3.3
  ↓
Python bridge
  ├── V1 SocialLayerService
  │   ├── 场景识别
  │   ├── 回复风格路由
  │   ├── 用户关系
  │   └── 显式记忆检索
  └── V2 StickerRuntime
      ├── Catalog 加载
      ├── approved / enabled / warning 过滤
      ├── 场景、情绪、关键词与关系评分
      └── Top-K 候选
  ↓
TypeScript 自动选择策略
  ├── 明确请求：宽松阈值与安全 fallback
  ├── 主动发送：高阈值与 Top1/Top2 分差
  ├── 全局、场景、单图冷却
  └── 每小时上限
  ↓
只注入“简短确认、不要描述图片”的提示
  ↓
Ollama / Qwen 单次推理生成正文
  ↓
agent_end（message_sent 为兼容回退）
  ↓
loadAdapter("openclaw-weixin")
  ↓
adapter.sendMedia(...)
  ↓
微信 CDN 上传与图片消息发送
```

## 两个独立仓库

### Yogurt Chat Bot

负责实时聊天：

- 社交上下文注入；
- 表情候选召回与选择；
- 冷却、安全和路径校验；
- 调用微信频道发送图片。

### Yogurt Sticker Tagger

独立仓库：`Doman1c/Yogurt_Sticker_Tagger`

负责离线资产管理：

- 图片校验、去重和 WebP 归一化；
- Ollama 视觉模型分类；
- 人工审核和受约束标签编辑；
- SQLite 审计；
- 导出稳定的 `stickers.json`。

聊天过程中不会调用视觉模型，Chat Bot 也不会直接访问 Tagger 的 SQLite。

## V1 社交层能力

### 场景识别

使用本地规则识别：

- `casual`
- `technical`
- `playful`
- `comfort`
- `celebration`
- `serious`

### 回复风格路由

根据场景生成可解释的回复策略，包括语气、长度、技术细节、是否优先安慰和是否避免机械化建议。

### 显式关系与记忆

SQLite 保存：

- 用户关系；
- 明确事实；
- 明确偏好；
- 共同经历。

普通聊天不会自动写入长期记忆。

### fail-open

Python、SQLite、bridge、Catalog 或图片发送异常时，原始 OpenClaw + Qwen 文本链路继续运行。

## V2 表情包能力

### 默认 `auto` 模式

- Retriever 本地输出候选；
- TypeScript 本地选择 Top 1；
- `choose_sticker` 不暴露给模型；
- Qwen 只推理一次；
- 不向模型注入候选描述；
- 正文之后由微信 outbound adapter 发送图片。

### `tool` 调试模式

保留 `choose_sticker(sticker_id)` 和候选校验，用于验证模型选图能力。该模式可能触发第二次推理，不建议作为 9B 本地模型的日常模式。

### 明确请求 fallback

用户明确要求表情时，即使普通评分因低权重或关键词不完全匹配而低于阈值，也会从仍满足以下条件的资产中选择最相关候选：

```text
status = approved
enabled = true
content_warnings = ["none"]
relationship 合法
文件存在且位于 assets/stickers/
```

安全过滤不会被绕过。

### 禁止描述图片

成功选图时，模型只需一句短确认或颜文字；无安全候选时，只能简短说明未能发送。两种情况都禁止描述、解释、复述或虚构图片画面。

## 项目目录

```text
Yogurt_Chat_Bot/
├── README.md
├── workspace/                         # OpenClaw 人格和行为规则
├── social_layer/
│   ├── config/stickers.example.json   # Sticker Runtime 配置模板
│   ├── src/yogurt_social/
│   │   ├── memory.py
│   │   ├── router.py
│   │   ├── service.py
│   │   ├── openclaw_adapter.py
│   │   └── stickers/
│   └── tests/
├── assets/stickers/
│   ├── images/                        # Tagger 导出的正式图片，不提交 Git
│   ├── stickers.json                  # 正式 Catalog，不提交 Git
│   └── README.md
├── integrations/openclaw/
│   ├── index-v2.ts                    # 当前插件入口
│   ├── sticker-selection.ts
│   ├── sticker-plugin-utils.ts
│   ├── openclaw.plugin.json
│   ├── package.json
│   ├── config.example.json
│   └── README.md
├── scripts/
│   ├── yogurt-social
│   ├── install-openclaw-adapter
│   ├── test-openclaw-adapter
│   ├── test-sticker-explicit-fallback
│   └── test-sticker-auto-delivery
└── docs/
    ├── V1_DESIGN.md
    ├── V2_DESIGN.md
    ├── V2_PROGRESS.md
    ├── OPENCLAW_ADAPTER.md
    └── archive/
        ├── V1_RELEASE.md
        └── V2_RELEASE.md
```

## 前置条件

- OpenClaw `>=2026.7.1-0`
- Node.js `>=22.22.3`
- Python `>=3.9`
- Ollama
- 已配置可用聊天模型
- 已连接 `openclaw-weixin`
- 独立安装的 Yogurt Sticker Tagger（需要管理表情资产时）

## 首次安装

```bash
cd ~/Yogurt_Chat_Bot
git switch main
git pull --ff-only origin main
bash scripts/install-openclaw-adapter
```

安装脚本会备份 OpenClaw 配置、建立本地插件链接、写入运行路径、创建 owner 用户并运行测试。

为了安全，首次安装默认保持：

```json
{
  "stickerEnabled": false
}
```

同步并验证表情资产后再开启：

```bash
openclaw config set \
  'plugins.entries.yogurt-social-injector.config.stickerEnabled' \
  'true' \
  --strict-json

openclaw config set \
  'plugins.entries.yogurt-social-injector.config.stickerSelectionMode' \
  '"auto"' \
  --strict-json
```

## 已安装环境的升级

插件使用 `--link` 指向仓库目录，普通升级不需要重新运行安装脚本：

```bash
cd ~/Yogurt_Chat_Bot
git switch main
git pull --ff-only origin main

bash scripts/test-openclaw-adapter
bash scripts/test-sticker-explicit-fallback
bash scripts/test-sticker-auto-delivery

openclaw config validate
openclaw plugins doctor
openclaw gateway restart
```

不要在仅升级代码时重复运行完整安装脚本，因为它会把 V2 恢复为安全默认关闭状态。

## 必需的 OpenClaw Hook 权限

```json
{
  "allowConversationAccess": true,
  "allowPromptInjection": true,
  "timeouts": {
    "before_prompt_build": 12000
  }
}
```

其中 `allowConversationAccess=true` 是 `agent_end` 对非 bundled 插件生效的必要条件。缺少时 runtime inspect 会出现：

```text
typed hook "agent_end" blocked because non-bundled plugins must set ...allowConversationAccess=true
```

修复：

```bash
openclaw config set \
  'plugins.entries.yogurt-social-injector.hooks.allowConversationAccess' \
  'true' \
  --strict-json
```

## 从 Tagger 同步表情资产

```bash
cd ~/Yogurt_Sticker_Tagger
source .venv/bin/activate

yogurt-sticker-tagger --config config.toml export

cp data/stickers.json \
  ~/Yogurt_Chat_Bot/assets/stickers/stickers.json

rsync -a data/images/ \
  ~/Yogurt_Chat_Bot/assets/stickers/images/
```

Catalog 与 `images/` 必须一起同步。当前实机验收使用 14 张 `approved + enabled` 资产。

## 运行时配置

插件 ID：

```text
yogurt-social-injector
```

关键配置：

| 配置项 | 作用 | 默认值 |
|---|---|---|
| `enabled` | 是否启用社交层 | `true` |
| `pythonPath` | Python 可执行文件 | `python3` |
| `repoPath` | Chat Bot 仓库绝对路径 | 必填/安装时写入 |
| `dbPath` | SQLite 路径 | `~/.openclaw/yogurt-social/memory.sqlite3` |
| `defaultUserId` | 默认用户 ID | `owner` |
| `allowedChannels` | 允许渠道 | `[]`，不限制 |
| `timeoutMs` | Python bridge 超时 | `2500` |
| `maxMemories` | 单轮最多记忆数 | `3` |
| `stickerEnabled` | 是否启用 V2 | `false` |
| `stickerConfigPath` | Sticker Runtime 配置路径 | 仓库内配置文件 |
| `stickerSelectionMode` | `auto` 或 `tool` | `auto` |

Sticker Runtime 配置见：

```text
social_layer/config/stickers.example.json
```

## 测试

```bash
cd ~/Yogurt_Chat_Bot

bash scripts/test-openclaw-adapter
bash scripts/test-sticker-explicit-fallback
bash scripts/test-sticker-auto-delivery
```

测试覆盖：

- V1 SQLite、场景、记忆和 bridge；
- fail-open；
- Catalog Schema 和文件路径；
- 明确请求安全 fallback；
- 禁止描述图片；
- auto 模式隐藏工具；
- agent_end / message_sent 去重；
- `loadAdapter("openclaw-weixin")`；
- `sendMedia` 调用和发送后冷却提交。

CI 覆盖 Python 3.9、3.10、3.11、3.12 和 Node.js 22。

## 运行时验证

```bash
openclaw config validate
openclaw plugins doctor
openclaw plugins inspect yogurt-social-injector --runtime --json
openclaw gateway status --deep --require-rpc
```

正确的 runtime inspect 应包含：

```text
version: 0.3.3
status: loaded
before_prompt_build
agent_end
message_sent
reply_payload_sending
```

实时日志：

```bash
openclaw logs --follow --local-time
```

成功链路：

```text
yogurt-social-injector: injection ready ... stickerSelected=... stickerDecision=explicit|proactive
yogurt-social-injector: sticker outbound scheduled trigger=agent_end ...
outbound: ... textLen=... mediaUrl=none
outbound: text sent OK ...
yogurt-social-injector: sticker media sent trigger=channel_outbound_adapter ...
```

正文日志中的 `mediaUrl=none` 在 V2 `0.3.3` 是正常的：正文和图片通过两次独立发送完成。

## 微信工具进度卡片

```bash
openclaw config set \
  'channels.openclaw-weixin.replyProgressMessages' \
  'false' \
  --strict-json
```

新会话默认关闭 verbose：

```bash
openclaw config set \
  'agents.defaults.verboseDefault' \
  '"off"' \
  --strict-json
```

## 常见故障

### 已选中候选但没有图片

先检查注册日志是否包含 `agent_end`。若 runtime inspect 显示 Hook 被阻止，开启 `allowConversationAccess=true`。

### `stickerCandidates=0`

检查：

- `stickers.json` 是否已同步；
- 图片文件是否存在；
- `status=approved`；
- `enabled=true`；
- `content_warnings=["none"]`；
- 当前 relationship 是否在资产允许范围内。

明确请求会启用安全 fallback，但不会绕过以上过滤。

### 插件版本与 install.version 不一致

以 `plugin.version` 和 `source` 为当前运行真值。`install.version` 是历史安装记录；重新链接后会更新。

### 文字正常但图片发送失败

查找：

```text
sticker media send failed trigger=channel_outbound_adapter (...)
```

图片发送失败不会删除已经发送的正文。

## 安全边界

- 模型不能指定任意本地路径；
- 只接受 Catalog 中的稳定 Sticker ID 和相对路径；
- 使用 `realpath` 限制在 `assets/stickers/`；
- 必须是普通文件；
- 超出大小限制的文件拒绝发送；
- 只发送 `approved + enabled + warnings=["none"]` 资产；
- 不向模型暴露候选描述或绝对路径；
- 不记录消息正文、Token、图片内容或数据库内容；
- Token、凭据、SQLite、真实图片、Catalog 和日志不得提交 Git。

## 关闭与回滚

只关闭 V2、保留 V1：

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

## 版本记录

### V2.0（稳定）

- V1 社交层全部能力；
- 独立 Sticker Tagger；
- 本地安全候选召回；
- auto 单次推理选图；
- tool 调试模式；
- 明确请求安全 fallback；
- 不描述图片；
- 冷却与数量限制；
- 微信真实图片发送；
- Python 3.9～3.12 CI；
- 微信实机验收。

### V1.0（归档）

- 社交场景识别；
- 回复风格路由；
- 显式关系与记忆；
- OpenClaw 前置注入；
- senderId 映射；
- fail-open。

## 相关文档

- `integrations/openclaw/README.md`：插件、Hook、发送链路和排障；
- `assets/stickers/README.md`：Catalog 同步与资产安全边界；
- `docs/V2_DESIGN.md`：V2 最终架构；
- `docs/archive/V1_RELEASE.md`：V1 验收归档；
- `docs/archive/V2_RELEASE.md`：V2 微信实机验收归档。
