# Yogurt Chat Bot（酸奶）

酸奶是一个运行在 **OpenClaw + Ollama + 微信官方 Bot** 之上的本地 AI 角色项目。

项目在只进行一次正常聊天模型推理的前提下，为酸奶提供：

- 可解释、可回滚的社交上下文；
- 基于最终回复的表情发送决策；
- 自动长期记忆候选与审计；
- 自然的微信多气泡回复；
- 技术、严肃和高风险场景保护；
- Python、SQLite、Catalog 或媒体发送异常时的 fail-open 文本链路。

实时聊天不会调用视觉模型，也不会为了自动记忆或多气泡再调用一次聊天模型。

## 当前版本

```text
Yogurt Chat Bot: V3
OpenClaw 插件: yogurt-social-injector 0.5.0-alpha.1
Sticker Runtime: V2.1
Catalog Schema: 1.0
```

当前实机环境：

- macOS Apple Silicon；
- OpenClaw `2026.7.1-2`；
- `@tencent-weixin/openclaw-weixin` `2.4.6`；
- Ollama；
- `qwen3.5:9b`；
- 微信官方 Bot 私聊。

V3 已完成实机验证：

- 自动记忆候选写入、晋升、待确认和修订审计；
- 2～3 个微信文字气泡按顺序发送；
- 原始整段回复不重复发送；
- 表情决策先读取完整回复，再拆分气泡；
- 文字气泡发送完成后发送表情图片；
- 一轮最多选择并发送一张表情；
- `celebration` 意图可以从“弄好了、太开心、夸夸我”等表达中触发；
- 图片发送失败不影响已经生成的文字。

## V3 新增能力

### 自动记忆

V3 使用确定性规则从用户消息中提取有限类型的长期信息，不调用第二个模型。

支持：

- 用户事实；
- 用户偏好；
- 重要里程碑；
- 重复确认；
- 新旧事实冲突检测；
- 稳定记忆更新；
- 不可变修订记录。

候选状态：

```text
pending     尚未达到自动晋升条件
promoted    已写入稳定记忆
merged      与已有稳定记忆合并
conflict    与已有记忆冲突，等待确认或后续更新
```

自动记忆不会：

- 把酸奶自己的回复写成用户事实；
- 保存密码、Token、验证码、密钥、身份证件或支付信息；
- 建立原始聊天全文表；
- 因提取失败而阻断正常回复。

临时信息可以保留为低重要度候选。例如“现在回重庆了”可以记录为 `current_location=重庆` 的 pending 候选，而不会直接覆盖长期居住地。

### 自然多气泡

V3 可以把适合微信私聊的短回复拆成 2～3 个自然气泡。

默认不会拆分：

- 代码块；
- Markdown 列表；
- 链接；
- 长技术回复；
- 已携带媒体的消息；
- 不足以形成两个自然语义段的短句。

递归发送的子气泡会通过一次性通行标记跳过再次拆分，因此不会无限递归或重复发送。

### 表情与多气泡协调

OpenClaw 的关键发送顺序：

```text
完整的模型回复
  ↓
message_sending priority 80
  ├── 复用入站上下文
  ├── V2.1 读取完整回复
  ├── StickerSendDecider 判断是否适合发送
  ├── Ranker 选择最相关候选
  └── Delivery Policy 检查阈值、分差、冷却和小时上限
  ↓
message_sending priority 70
  ├── 拆成 2～3 个自然文字气泡
  ├── 依次调用微信 sendText
  └── 取消原始整段消息
  ↓
延迟发送已选表情图片
```

这样可以保证：

- 表情评分看到的是完整回复，而不是某个子气泡；
- 子气泡不会重复触发表情决策；
- 文字先到达，表情随后到达；
- 原始整段消息不会和子气泡一起重复出现。

## 完整架构

```text
微信官方 Bot 私聊消息
  ↓
@tencent-weixin/openclaw-weixin
  ↓
OpenClaw before_prompt_build
  ↓
Yogurt Social Injector 0.5.0-alpha.1
  ├── V1 Social Layer
  │   ├── 场景识别
  │   ├── 回复风格路由
  │   ├── 用户关系
  │   └── 稳定记忆检索
  ├── V2.1 Sticker Runtime
  │   ├── Catalog 安全过滤
  │   ├── 语义优先候选召回
  │   ├── score_breakdown
  │   └── pre-reply 候选准备
  └── V3 Pending Context
      ├── 自动记忆待处理 turn
      ├── 多气泡待处理 turn
      └── 表情 finalizer 入站上下文
  ↓
Ollama / Qwen 单次推理生成最终正文
  ↓
V3 message_sending 协调器
  ├── 完整回复上的 V2.1 post-reply 表情决策
  ├── 自动记忆提交回退
  ├── 多气泡拆分与发送
  └── 原始整段消息取消
  ↓
WeChat outbound adapter
  ├── sendText：文字气泡
  └── sendMedia：表情图片
```

## V1 社交层

本地 Router 使用以下场景：

```text
casual
technical
playful
comfort
celebration
serious
```

社交层根据场景输出语气、长度、技术细节、是否优先安慰，以及是否避免机械化建议。

Python、SQLite 或 bridge 异常时，OpenClaw + Qwen 的原始文本链路继续运行。

## V2.1 表情系统

### 两阶段决策

第一阶段回答：

```text
这一轮是否适合发表情？
```

输入包括：

- 用户当前消息；
- 酸奶最终回复；
- Scene 和 Mood；
- 候选质量与 Top1/Top2 分差；
- 技术、严肃和高风险标记；
- 用户显式请求或禁止请求。

第二阶段回答：

```text
如果要发，哪张最相关？
```

### 主动发送通道

- `celebration`：积极；
- `playful`：积极；
- `comfort`：开放，但要求更高相关性；
- `casual`：开放，但发送更克制；
- `technical`：主动发送硬阻断；
- `serious`：主动发送硬阻断。

高风险求助词在任何 Scene 和显式模式下都不会触发表情发送。

### 语义优先 Ranker

候选排序大致优先级：

```text
用户精确关键词和短语
> 回复关键词
> Emotion
> Description / OCR
> Tone
> Scene
> Relationship
> 受限 Weight prior
```

Scene 不会压过“无语、疲惫、拥抱、惊讶、生气”等具体语义。

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
→ 在最终回复生成后由 Send Decider 判断
```

### 默认 `auto` 模式

- `choose_sticker` 不暴露给模型；
- Qwen 只进行一次聊天推理；
- VLM 不进入实时聊天；
- 不向模型注入候选绝对路径；
- 正文发送后由 outbound adapter 发送图片。

`tool` 模式保留 `choose_sticker(sticker_id)` 用于调试，不建议作为 9B 本地模型的日常模式。

## 两个仓库的职责

### Yogurt Chat Bot

负责实时聊天：

- OpenClaw 社交上下文注入；
- 自动记忆候选与稳定记忆；
- 多气泡发送；
- 表情发送决策和候选选择；
- 冷却、安全和路径校验；
- 调用微信频道发送文字与图片。

### Yogurt Sticker Tagger

独立仓库：`Doman1c/Yogurt_Sticker_Tagger`

负责离线资产管理：

- 图片校验、去重和 WebP 归一化；
- Ollama 视觉模型分类；
- 人工审核和受约束标签编辑；
- SQLite 审计；
- 表情决策实验室；
- 验证、发布和回滚；
- 导出稳定的 `stickers.json`。

聊天过程中不会调用视觉模型，Chat Bot 也不会直接访问 Tagger SQLite。

## 项目目录

```text
Yogurt_Chat_Bot/
├── README.md
├── workspace/                         # OpenClaw 人格和行为规则
├── social_layer/
│   ├── config/
│   │   ├── stickers.example.json
│   │   └── stickers.json              # 本地运行配置，可不提交
│   ├── src/yogurt_social/
│   │   ├── memory.py
│   │   ├── memory_review.py
│   │   ├── auto_memory.py
│   │   ├── auto_memory_extended.py
│   │   ├── router.py
│   │   ├── service.py
│   │   ├── openclaw_adapter.py
│   │   └── stickers/
│   │       ├── catalog.py
│   │       ├── config.py
│   │       ├── decision.py
│   │       ├── models.py
│   │       ├── retriever.py
│   │       └── runtime.py
│   └── tests/
├── assets/stickers/
│   ├── images/                        # 本地表情资产，不提交 Git
│   ├── stickers.json                  # 正式 Catalog，可不提交 Git
│   └── README.md
├── integrations/openclaw/
│   ├── index-v2.ts                    # V2.1 表情运行时
│   ├── index-v3.ts                    # 当前插件入口和协调器
│   ├── v3-runtime.ts                  # 自动记忆与多气泡
│   ├── v3-memory-extended.ts           # 额外确定性记忆召回
│   ├── multi-bubble.ts
│   ├── sticker-selection.ts
│   ├── sticker-plugin-utils.ts
│   ├── sticker-turn.ts
│   ├── sticker-v21-utils.ts
│   ├── openclaw.plugin.json
│   ├── package.json
│   └── config.example.json
├── scripts/
│   ├── install-openclaw-adapter
│   ├── yogurt-memory
│   ├── watch-sticker-scores
│   ├── test-openclaw-adapter
│   ├── test-sticker-explicit-fallback
│   ├── test-sticker-auto-delivery
│   ├── test-sticker-v21
│   ├── test-v3
│   ├── test-v3-runtime
│   └── test-v3-sticker-bubble-order
└── docs/
    ├── V1_DESIGN.md
    ├── V2_DESIGN.md
    ├── V2_1_DESIGN.md
    ├── V3_RESEARCH_PLAN.md
    └── OPENCLAW_ADAPTER.md
```

## 前置条件

- OpenClaw `>=2026.7.1-0`；
- Node.js `>=22.22.3`；
- Python `>=3.9`；
- Ollama；
- 已配置可用聊天模型；
- 已连接 `openclaw-weixin`；
- 管理表情资产时可选 Yogurt Sticker Tagger。

## 首次安装

```bash
cd ~/Yogurt_Chat_Bot
git switch main
git pull --ff-only origin main
bash scripts/install-openclaw-adapter
```

安装脚本会：

- 备份 OpenClaw 配置；
- 以本地路径链接插件；
- 写入 Python、仓库和 SQLite 路径；
- 创建 owner 用户；
- 运行基础测试。

首次安装默认保持高风险功能关闭：

```json
{
  "stickerEnabled": false,
  "autoMemoryEnabled": false,
  "multiBubbleEnabled": false
}
```

## 已安装环境升级

插件通过本地路径链接到仓库，普通升级不需要重新运行安装脚本：

```bash
cd ~/Yogurt_Chat_Bot
git switch main
git pull --ff-only origin main

bash scripts/test-openclaw-adapter
bash scripts/test-sticker-explicit-fallback
bash scripts/test-sticker-auto-delivery
bash scripts/test-sticker-v21
bash scripts/test-v3
bash scripts/test-v3-runtime
bash scripts/test-v3-sticker-bubble-order

openclaw config validate
openclaw plugins doctor
openclaw gateway restart
```

不要在仅升级代码时重复运行完整安装脚本，因为安装脚本会把可选功能恢复为安全默认关闭状态。

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

## 启用功能

### 表情系统

确认 `assets/stickers/stickers.json`、图片目录和运行配置已经同步后：

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

### 自动记忆

```bash
openclaw config set \
  'plugins.entries.yogurt-social-injector.config.autoMemoryEnabled' \
  'true' \
  --strict-json
```

默认自动晋升阈值：

```text
0.78
```

### 多气泡

```bash
openclaw config set \
  'plugins.entries.yogurt-social-injector.config.multiBubbleEnabled' \
  'true' \
  --strict-json
```

默认参数：

```json
{
  "multiBubbleMinChars": 24,
  "multiBubbleMaxChars": 360,
  "multiBubbleMaxBubbles": 3,
  "multiBubbleDelayMs": 420
}
```

修改配置后执行：

```bash
openclaw config validate
openclaw gateway restart
```

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
| `senderMap` | 微信发送者到用户 ID 的映射 | `{}` |
| `unknownSenderPolicy` | 未知用户处理方式 | `default` |
| `allowedChannels` | 允许渠道 | `[]`，不限制 |
| `timeoutMs` | Python bridge 超时 | `2500` |
| `maxMemories` | 单轮最多注入的稳定记忆 | `3` |
| `stickerEnabled` | 是否启用表情系统 | `false` |
| `stickerConfigPath` | Sticker Runtime 配置路径 | 仓库内配置文件 |
| `stickerSelectionMode` | `auto` 或 `tool` | `auto` |
| `autoMemoryEnabled` | 是否启用自动记忆 | `false` |
| `autoMemoryPromotionThreshold` | 自动晋升阈值 | `0.78` |
| `autoMemoryTimeoutMs` | 自动记忆超时 | `2500` |
| `multiBubbleEnabled` | 是否启用多气泡 | `false` |
| `multiBubbleMinChars` | 最短拆分长度 | `24` |
| `multiBubbleMaxChars` | 可拆分的最大长度 | `360` |
| `multiBubbleMaxBubbles` | 单轮最大气泡数 | `3` |
| `multiBubbleDelayMs` | 气泡发送间隔 | `420` |

完整示例：

```text
integrations/openclaw/config.example.json
```

## 自动记忆检查

只读检查命令不会修改数据库。

查看全部候选：

```bash
bash scripts/yogurt-memory --pretty \
  candidates --user-id owner
```

只看 pending：

```bash
bash scripts/yogurt-memory --pretty \
  candidates --user-id owner --status pending
```

查看稳定记忆快照：

```bash
bash scripts/yogurt-memory --pretty \
  snapshot --user-id owner
```

查看最近修订：

```bash
bash scripts/yogurt-memory --pretty \
  revisions --user-id owner --limit 50
```

通过环境变量检查其他数据库：

```bash
YOGURT_SOCIAL_DB=/path/to/memory.sqlite3 \
  bash scripts/yogurt-memory --pretty snapshot --user-id owner
```

## 表情评分观察

```bash
cd ~/Yogurt_Chat_Bot
bash scripts/watch-sticker-scores
```

该脚本会打印当前实际运行配置，并过滤：

```text
send decision trigger=
send decision rejected by delivery policy
post-reply sticker selected
sticker media sent
sticker media send failed
multi bubble sent
```

示例：

```text
send decision trigger=agent_end mode=proactive
shouldSend=true score=6.85 threshold=5.80
intents=celebration
post-reply sticker selected sticker=...
multi bubble sent count=2
sticker media sent trigger=channel_outbound_adapter
```

仓库默认阈值和本地调参可能不同，以实时日志中的 `threshold` 以及 `watch-sticker-scores` 打印的配置为准。

常见拒绝原因：

```text
proactive_threshold   候选最高分低于主动发送下限
score_margin          第一、第二名分差不足
global_cooldown       全局冷却
candidate_cooldown    单图或场景冷却
hourly_limit          每小时发送上限
```

## 从 Tagger 部署表情资产

推荐流程：

```text
审核并批准
→ 决策实验室模拟
→ 验证
→ 导出并发布到酸奶
```

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

## 测试

完整测试：

```bash
cd ~/Yogurt_Chat_Bot

bash scripts/test-openclaw-adapter
bash scripts/test-sticker-explicit-fallback
bash scripts/test-sticker-auto-delivery
bash scripts/test-sticker-v21
bash scripts/test-v3
bash scripts/test-v3-runtime
bash scripts/test-v3-sticker-bubble-order
```

CI 使用：

- Python 3.9；
- Python 3.10；
- Python 3.11；
- Python 3.12；
- Node.js 22。

## 实机检查

查看插件入口和 Hook：

```bash
openclaw plugins inspect \
  yogurt-social-injector \
  --runtime \
  --json
```

正常情况下可以看到：

```text
source: .../integrations/openclaw/index-v3.ts
version: 0.5.0-alpha.1
message_sending priority 80
message_sending priority 70
```

Gateway 启动后应出现：

```text
yogurt-social-injector: V3 sticker finalizer captured=true
```

## 回滚

不需要卸载插件即可关闭 V3 功能：

```bash
openclaw config set \
  'plugins.entries.yogurt-social-injector.config.autoMemoryEnabled' \
  'false' \
  --strict-json

openclaw config set \
  'plugins.entries.yogurt-social-injector.config.multiBubbleEnabled' \
  'false' \
  --strict-json

openclaw config validate
openclaw gateway restart
```

如需同时关闭表情：

```bash
openclaw config set \
  'plugins.entries.yogurt-social-injector.config.stickerEnabled' \
  'false' \
  --strict-json

openclaw gateway restart
```

关闭 V3 开关不会删除已有稳定记忆、候选或修订记录。

## 安全边界

- 不把密码、Token、验证码、密钥、身份文件和支付信息写入长期记忆；
- 不允许助手回复成为用户事实；
- 不在实时聊天中调用 VLM；
- 不向聊天模型暴露表情绝对路径；
- 技术和严肃场景默认不主动发表情；
- 高风险求助场景禁止发表情；
- 一轮最多选择一张图片；
- 图片失败保留文字；
- 自动记忆失败保留聊天；
- 多气泡部分发送失败时避免重复发送整段原文。

## 版本演进

```text
V1    社交上下文、关系和显式记忆
V2    微信真实图片发送链路
V2.1  最终回复后的发送决策、语义 Ranker 和 Delivery Policy
V3    自动记忆候选、修订审计、自然多气泡和表情/气泡协调器
```

## License

当前仓库为私人项目，未声明公共开源许可证。
