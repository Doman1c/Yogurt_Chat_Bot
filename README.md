# Yogurt Chat Bot（酸奶）

酸奶是一个运行在 **OpenClaw + Ollama + 微信官方 Bot** 之上的本地 AI 角色项目。

它在不增加第二次聊天模型推理的前提下，为每轮消息补充可解释、可回滚的社交上下文，并根据当前对话、酸奶最终回复、用户关系和离线审核过的表情包资产，自主判断是否适合发送表情，以及应该发送哪一张。

## 当前稳定状态

```text
Yogurt Chat Bot: V2.1
OpenClaw 插件: yogurt-social-injector 0.4.0
Yogurt Sticker Tagger: 0.3.0
Catalog Schema: 1.0
正式资产基线: 14 张 approved + enabled 表情包
```

实机环境：

- macOS
- OpenClaw `2026.7.1-2`
- `@tencent-weixin/openclaw-weixin` `2.4.6`
- Ollama
- `qwen3.5:9b`
- 微信官方 Bot 私聊

V2.1 在 V2 真实图片发送链路上新增：

- 发送决策与选图决策分离；
- 主动发送在酸奶最终回复生成后判断；
- `comfort` 和受控的 `casual` 通道开放；
- 技术、严肃和高风险求助内容硬阻断；
- 具体语义、Emotion、Description、OCR 和 Tone 优先于宽泛 Scene；
- Scene 从主导信号降为上下文修正；
- `weight` 从乘法改为受限加法先验；
- 具体显式请求找不到相关图时不再强行发送；
- 每个候选提供可解释的 `score_breakdown`；
- 仍然只有一次 Qwen 聊天推理；
- 图片失败不阻断已经生成的文字回复。

## 完整架构

```text
微信官方 Bot 私聊消息
  ↓
@tencent-weixin/openclaw-weixin
  ↓
OpenClaw before_prompt_build
  ↓
Yogurt Social Injector 0.4.0
  ↓
Python bridge
  ├── V1 SocialLayerService
  │   ├── 场景识别
  │   ├── 回复风格路由
  │   ├── 用户关系
  │   └── 显式记忆检索
  └── V2.1 Sticker Runtime
      ├── Catalog 安全过滤
      ├── 语义优先候选召回
      ├── score_breakdown
      └── 发送决策所需上下文
  ↓
Ollama / Qwen 单次推理生成最终正文
  ↓
agent_end
  ├── 读取最终回复
  ├── StickerSendDecider 判断是否发送
  ├── 语义 Ranker 选择图片
  └── message_sent 作为兼容回退
  ↓
TypeScript StickerAutoPolicyStore
  ├── Top1 阈值和 Top1/Top2 分差
  ├── 全局、场景、单图冷却
  ├── 每小时上限
  └── 成功发送后才提交冷却
  ↓
api.runtime.channel.outbound.loadAdapter("openclaw-weixin")
  ↓
adapter.sendMedia(...)
  ↓
微信 CDN
  ↓
微信原生图片消息
```

## 两个仓库的职责

### Yogurt Chat Bot

负责实时聊天：

- OpenClaw 社交上下文注入；
- 发送决策；
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
- 表情决策实验室；
- 一键验证、发布和回滚；
- 导出稳定的 `stickers.json`。

聊天过程中不会调用视觉模型，Chat Bot 也不会直接访问 Tagger SQLite。

## V1 社交层

### 场景识别

本地 Router 使用以下场景：

```text
casual
technical
playful
comfort
celebration
serious
```

### 回复风格路由

根据场景输出语气、长度、技术细节、是否优先安慰以及是否避免机械化建议。

### 显式关系与记忆

SQLite 只保存明确的：

- 用户关系；
- 事实；
- 偏好；
- 共同经历。

普通聊天不会自动写入长期记忆。

### fail-open

Python、SQLite、bridge、Catalog 或图片发送异常时，原始 OpenClaw + Qwen 文本链路继续运行。

## V2.1 表情系统

### 两阶段决策

第一阶段只回答：

```text
这一轮是否适合发表情？
```

输入包括：

- 用户当前消息；
- 酸奶最终回复；
- Scene 和 Mood；
- 候选质量与分差；
- 技术、严肃和高风险标记；
- 显式请求或禁止请求。

第二阶段只回答：

```text
如果要发，哪张最相关？
```

### 主动发送通道

- `celebration`：积极；
- `playful`：积极；
- `comfort`：开放，但要求更高相关性；
- `casual`：开放，但阈值更高、发送更克制；
- `technical`：主动发送硬阻断；
- `serious`：主动发送硬阻断。

高风险求助词在任何 Scene 和显式模式下都不会触发表情发送。

### 语义优先 Ranker

V2.1 的大致优先级：

```text
精确关键词和短语
> Emotion
> Description / OCR
> Tone
> Scene
> Relationship
> 受限 Weight prior
```

Scene 不再以最高权重压过“无语、疲惫、拥抱、惊讶、生气”等具体语义。

### 显式请求模式

```text
explicit_specific
例如：发一个无语的表情
→ 必须达到最低相关性
→ 找不到时不强行发送

explicit_generic
例如：随便发个表情包
→ 在安全候选中结合当前聊天氛围选择

proactive
用户没有要求
→ 由最终回复后的 Send Decider 判断
```

### 默认 `auto` 模式

- `choose_sticker` 不暴露给模型；
- Qwen 只推理一次；
- VLM 不进入实时聊天；
- 不向模型注入候选绝对路径；
- 正文发送后由 outbound adapter 发送图片。

### `tool` 调试模式

保留 `choose_sticker(sticker_id)` 用于调试。该模式可能触发额外推理，不建议用于 9B 本地模型的日常运行。

### 禁止描述图片

模型不能描述、解释、复述或虚构即将发送的图片画面。图片由本地系统选中并通过微信媒体链路发送。

## 项目目录

```text
Yogurt_Chat_Bot/
├── README.md
├── workspace/                         # OpenClaw 人格和行为规则
├── social_layer/
│   ├── config/stickers.example.json
│   ├── src/yogurt_social/
│   │   ├── memory.py
│   │   ├── router.py
│   │   ├── service.py
│   │   ├── openclaw_adapter.py
│   │   └── stickers/
│   │       ├── catalog.py
│   │       ├── config.py
│   │       ├── decision.py
│   │       ├── lab.py
│   │       ├── models.py
│   │       ├── retriever.py
│   │       └── runtime.py
│   └── tests/
├── assets/stickers/
│   ├── images/                        # 本地资产，不提交 Git
│   ├── stickers.json                  # 正式 Catalog，不提交 Git
│   └── README.md
├── integrations/openclaw/
│   ├── index-v2.ts
│   ├── sticker-selection.ts
│   ├── sticker-plugin-utils.ts
│   ├── sticker-turn.ts
│   ├── sticker-v21-utils.ts
│   ├── openclaw.plugin.json
│   ├── package.json
│   └── README.md
├── scripts/
│   ├── install-openclaw-adapter
│   ├── test-openclaw-adapter
│   ├── test-sticker-explicit-fallback
│   ├── test-sticker-auto-delivery
│   └── test-sticker-v21
└── docs/
    ├── V1_DESIGN.md
    ├── V2_DESIGN.md
    ├── V2_1_DESIGN.md
    ├── V2_PROGRESS.md
    └── OPENCLAW_ADAPTER.md
```

## 前置条件

- OpenClaw `>=2026.7.1-0`
- Node.js `>=22.22.3`
- Python `>=3.9`
- Ollama
- 已配置可用聊天模型
- 已连接 `openclaw-weixin`
- Yogurt Sticker Tagger 0.3.0（管理和部署表情资产时）

## 首次安装

```bash
cd ~/Yogurt_Chat_Bot
git switch main
git pull --ff-only origin main
bash scripts/install-openclaw-adapter
```

安装脚本会备份 OpenClaw 配置、建立本地插件链接、写入运行路径、创建 owner 用户并运行测试。

首次安装默认保持：

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

## 已安装环境升级

插件通过 `--link` 指向仓库目录，普通升级不需要重新运行安装脚本：

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

不要在仅升级代码时重复运行完整安装脚本，因为安装脚本会把表情系统恢复为安全默认关闭状态。

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

`allowConversationAccess=true` 是非 bundled 插件使用 `agent_end` 的必要条件。

修复命令：

```bash
openclaw config set \
  'plugins.entries.yogurt-social-injector.hooks.allowConversationAccess' \
  'true' \
  --strict-json
```

## 从 Tagger 部署表情资产

推荐使用 Yogurt Sticker Tagger 0.3.0 Web Admin：

```text
审核并批准
→ 决策实验室模拟
→ 仅验证
→ 导出并发布到酸奶
```

Tagger 会自动：

- 导出最新 `approved + enabled` Catalog；
- 校验 Schema、warning、文件和路径；
- 备份生产 Catalog；
- 同步标准化图片；
- 原子切换 Catalog；
- 运行 Chat Bot 三组表情测试；
- 检查 OpenClaw 配置和插件；
- 重启并验证 Gateway；
- 失败时恢复上一版 Catalog。

手工同步只作为备用：

```bash
cp \
  ~/Yogurt_Sticker_Tagger/data/stickers.json \
  ~/Yogurt_Chat_Bot/assets/stickers/stickers.json

rsync -a \
  ~/Yogurt_Sticker_Tagger/data/images/ \
  ~/Yogurt_Chat_Bot/assets/stickers/images/
```

Catalog 和 `images/` 必须一起同步。

## 运行时配置

插件 ID：

```text
yogurt-social-injector
```

| 配置项 | 作用 | 默认值 |
|---|---|---|
| `enabled` | 是否启用社交层 | `true` |
| `pythonPath` | Python 可执行文件 | `python3` |
| `repoPath` | Chat Bot 仓库绝对路径 | 安装时写入 |
| `dbPath` | SQLite 路径 | `~/.openclaw/yogurt-social/memory.sqlite3` |
| `defaultUserId` | 默认用户 ID | `owner` |
| `allowedChannels` | 允许渠道 | `[]`，不限制 |
| `timeoutMs` | Python bridge 超时 | `2500` |
| `maxMemories` | 单轮最多记忆数 | `3` |
| `stickerEnabled` | 是否启用表情系统 | `false` |
| `stickerConfigPath` | Sticker Runtime 配置 | 仓库内配置文件 |
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
bash scripts/test-sticker-v21
bash scripts/test-sticker-auto-delivery
```

V2.1 测试覆盖：

- 精确语义压过 Scene 和高 Weight；
- 具体显式请求不发送无关 fallback；
- 泛化显式请求仍可选择安全候选；
- comfort 通道开放；
- 高风险内容全场景硬阻断；
- casual 可进入决策但中性聊天保持安静；
- technical 和 serious 主动发送阻断；
- agent_end / message_sent 去重；
- outbound adapter 图片发送；
- 发送成功后才提交冷却。

CI 覆盖 Python 3.9、3.10、3.11、3.12 和 Node.js 22。

## 决策实验室

Tagger 0.3.0 可以在浏览器中调用本仓库的生产算法进行模拟，但不会：

- 发送微信消息；
- 写入冷却；
- 修改 Catalog；
- 调用 VLM 或第二次聊天模型推理。

输出包含：

```text
Scene / Mood
should_send
score / threshold
reasons
Top-K
score_breakdown
```

## 运行时验证

```bash
openclaw config validate
openclaw plugins doctor
openclaw plugins inspect yogurt-social-injector --runtime --json
openclaw gateway status --deep --require-rpc
```

正确的 runtime inspect 应包含：

```text
version: 0.4.0
status: loaded
hookCount: 4
before_prompt_build
agent_end
message_sent
reply_payload_sending
```

实时日志：

```bash
openclaw logs --follow --local-time
```

V2.1 关键日志：

```text
send decision
shouldSend=
score=
threshold=
top=
runnerUp=
post-reply sticker selected
sticker media sent
```

正文日志中的 `mediaUrl=none` 是正常的：正文和图片通过两次独立发送完成。

## 微信工具进度卡片

关闭微信侧进度消息：

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

### 用户没有要求发表情，但一直不发送

检查最终回复后的日志是否出现 `send decision`。如果只有 `awaiting_assistant_reply`，确认 `agent_end` 未被权限阻断，并检查 `message_sent` 回退。

真实微信测试受以下冷却影响：

```text
全局主动发送冷却
Scene 冷却
单图冷却
每小时发送上限
```

连续测试不能把冷却拒绝误判为识别失败。

### 发送了错误的宽泛 Scene 表情

查看候选 `score_breakdown`。V2.1 中具体关键词、Emotion、Description、OCR 和 Tone 应高于 Scene；高 `weight` 只能提供受限先验。

### `stickerCandidates=0`

检查：

- `stickers.json` 是否已同步；
- 图片文件是否存在；
- `status=approved`；
- `enabled=true`；
- `content_warnings=["none"]`；
- relationship 是否合法。

### 已选中候选但没有图片

检查 `agent_end`、`message_sent` 和：

```text
sticker outbound scheduled
sticker media sent
sticker media send failed
```

图片发送失败不会删除已经发送的正文。

### 插件版本与 install.version 不一致

以 runtime inspect 的 `plugin.version` 和 `source` 为运行真值。`install.version` 可能只是旧的安装记录。

## 安全边界

- `auto` 只有一次聊天模型推理；
- VLM 只用于离线 Tagger；
- 模型不能指定任意本地路径；
- 只发送 Catalog 安全资产；
- `content_warnings` fail-closed；
- 显式请求不绕过安全过滤；
- 高风险求助不发表情；
- 图片失败不阻断文字；
- 不描述或虚构图片；
- 不从普通聊天自动写长期记忆；
- 不修改用户原始消息；
- 不记录消息正文、Token、图片内容或数据库内容；
- 使用 `realpath` 限制资产目录；
- 真实发送成功后才记录冷却；
- Token、SQLite、真实图片、Catalog 和日志不得提交 Git。

## 关闭与回滚

只关闭表情系统、保留 V1：

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

Catalog 发布回滚由 Sticker Tagger 0.3.0 Web Admin 执行。

## 版本记录

### V2.1（稳定）

- 独立发送决策和选图决策；
- 最终回复参与主动发送判断；
- comfort 和受控 casual 通道；
- 高风险全场景阻断；
- 语义优先加法 Ranker；
- 具体、泛化显式请求分离；
- 完整决策与候选可观测性；
- 一次 Qwen 推理；
- Tagger 0.3.0 决策实验室与一键发布。

### V2.0（归档）

- 独立 Sticker Tagger；
- 本地安全候选召回；
- auto 单次推理选图；
- tool 调试模式；
- 明确请求安全 fallback；
- 冷却与数量限制；
- 微信真实图片发送。

### V1.0（归档）

- 社交场景识别；
- 回复风格路由；
- 显式关系与记忆；
- OpenClaw 前置注入；
- senderId 映射；
- fail-open。

## 相关文档

- `integrations/openclaw/README.md`：插件、Hook、发送链路和排障；
- `assets/stickers/README.md`：Catalog 与资产安全边界；
- `docs/V2_1_DESIGN.md`：V2.1 发送决策和语义 Ranker；
- `docs/V2_DESIGN.md`：V2 基础架构；
- `docs/archive/V1_RELEASE.md`：V1 归档；
- `docs/archive/V2_RELEASE.md`：V2 微信实机验收归档。
