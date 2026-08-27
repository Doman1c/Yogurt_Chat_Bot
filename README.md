# Yogurt Chat Bot（酸奶）

酸奶是一个本地运行的 AI 角色项目。当前仓库同时包含微信文字聊天层、社交与记忆系统、独立语音能力、Daily TTS Runtime 和 Singing Runtime。

**微信只是酸奶的文字交互端。** 它负责文字聊天、表情、多气泡、记忆与社交上下文，不接入 Mouth、TTS 或 Singing，也不会把微信作为语音输出通道。

语音能力属于酸奶自身的独立能力层，面向未来的 Native Chat、桌面 Live2D、桌面应用、机器人或其他本地载体。

## 设计边界

当前架构明确把“微信文字端”和“酸奶本体能力”分开：

```text
                    Yogurt Brain / Local LLM
                             │
              ┌──────────────┴──────────────┐
              │                             │
       WeChat Text Surface           Native / Embodiment
              │                             │
      Social / Memory / Sticker             ├─ Mouth
      Multi-bubble / Text Output            ├─ VoiceSession
              │                             ├─ Daily TTS
              │                             ├─ Singing
              │                             └─ future Ears / Eyes / Body
              │
      微信文字与表情输出
```

核心原则：

- 微信保持 **text-first / text-only**，不调用 Mouth；
- Mouth 不实现微信 Hook，也不依赖微信 Session；
- Daily TTS 与 Singing 是独立 Runtime；
- Singing 不进入实时聊天链路；
- 未来 Live2D、桌面应用和机器人可以直接调用 Mouth / VoiceSession；
- 语音、视觉或实体化能力失败时，不影响微信文字聊天。

## 当前版本

```text
Yogurt Chat Bot: V3
OpenClaw 插件: yogurt-social-injector 0.5.0-alpha.1
Sticker Runtime: V2.1
Catalog Schema: 1.0
Yogurt Daily TTS: V1
Yogurt Singing Pipeline: V2.1
```

当前微信实机环境：

- macOS Apple Silicon；
- OpenClaw `2026.7.1-2`；
- `@tencent-weixin/openclaw-weixin` `2.4.6`；
- Ollama；
- `qwen3.5:9b`；
- 微信官方 Bot 私聊。

当前独立 Voice / Singing Runtime 已在 Windows + RTX 4080 环境完成实际推理验证。模型、参考音频、训练数据、歌曲、Stem、生成音频和本地 Runtime 配置均保持在 Git 之外。

## 当前项目状态

### 微信文字聊天

已完成：

- OpenClaw + Ollama + 微信官方 Bot；
- 社交上下文与场景路由；
- 自动长期记忆候选、晋升、冲突与修订审计；
- 2～3 个自然微信文字气泡；
- 基于最终回复的表情发送决策；
- 表情与多气泡顺序协调；
- 技术、严肃和高风险场景保护；
- Python、SQLite、Catalog、表情发送异常时的 fail-open 文本链路。

微信链路有意停留在文字与图片层，不接入 Mouth / TTS。

### Yogurt Mouth / VoiceSession

Mouth 是酸奶的独立说话能力，不是微信 TTS 插件。

```text
Trusted Caller
  ↓
Yogurt Mouth
  ↓
Voice Orchestrator
  ↓
Voice Runtime
  ↓
Yogurt Daily TTS
  ↓
In-memory audio
```

当前支持：

- typed `SpeakRequest` / `SpeakResult`；
- scene 到 bounded voice plan 的映射；
- 本地 loopback Voice Runtime；
- PlaybackController；
- VoiceSession queue / priority / cancellation；
- amplitude envelope，为未来 Live2D 嘴型提供接口；
- 不向调用方暴露模型、参考音频和本地路径。

潜在调用方包括 Native Chat、Live2D 桌宠、桌面应用和机器人。微信不是 Mouth caller。

### Yogurt Daily TTS V1

当前 Daily TTS 使用 GPT-SoVITS 80:20 双语模型作为正式基线。

```text
{text, language, emotion, intensity}
  ↓
Emotion Router
  ↓
Reference Selection
  ↓
GPT-SoVITS
  ↓
WAV
```

当前已验证：

- 中文 / 日文 / 英文；
- 48 条启用的情绪参考；
- `neutral / happy / excited / soft / shy / sad / serious / comforting`；
- intensity reference routing；
- unknown emotion 与 `upset` 的安全 fallback；
- CUDA warmup 与模型常驻；
- private reference / transcript / model path 不通过公共接口暴露。

### Yogurt Singing Pipeline V2.1

当前 Singing 使用稳定 Seed-VC Runtime，并在外层加入完整 Pipeline：

```text
Source Song
  ↓
Vocal / Instrumental Separation
  ↓
Lead / Backing Separation
  ├─ Lead → Seed-VC → Yogurt Lead
  └─ Backing → preserve / remove
  ↓
Yogurt Lead + Original Backing + Instrumental
  ↓
Final WAV / MP3
```

V2.1 当前支持：

- `incoming / processing / archive / failed` 生命周期；
- SHA-256 去重；
- SQLite job history；
- 单实例锁；
- `--resume`；
- 分阶段失败恢复；
- Harmony-aware lead-only Seed-VC；
- `preserve` / `remove` 两种和声模式；
- harmony separation fail-closed；
- 最终 WAV / MP3 自动混音。

实际试听中，Lead-only + 原和声回混已经解决了此前绝大多数和声区域的机械音问题。

## 微信 V3 架构

微信端只负责文字与表情输出：

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

**这里没有 Mouth、Voice Runtime、GPT-SoVITS 或 Seed-VC。**

实时微信聊天不会调用视觉模型，也不会为了自动记忆或多气泡再调用一次聊天模型。

## V3 自动记忆

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

## V3 自然多气泡

V3 可以把适合微信私聊的短回复拆成 2～3 个自然气泡。

默认不会拆分：

- 代码块；
- Markdown 列表；
- 链接；
- 长技术回复；
- 已携带媒体的消息；
- 不足以形成两个自然语义段的短句。

递归发送的子气泡通过一次性通行标记跳过再次拆分，避免无限递归或重复发送。

## V2.1 表情系统

表情决策分成两步：

```text
是否适合发表情？
        ↓
如果要发，哪张最相关？
```

输入包括用户消息、酸奶最终回复、Scene、Mood、候选质量、Top1/Top2 分差、技术/严肃/高风险标记以及显式请求。

主动发送策略：

- `celebration`：积极；
- `playful`：积极；
- `comfort`：开放，但要求更高相关性；
- `casual`：开放，但发送更克制；
- `technical`：主动发送硬阻断；
- `serious`：主动发送硬阻断。

高风险求助词在任何 Scene 和显式模式下都不会触发表情发送。

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

## 表情与多气泡协调

```text
完整模型回复
  ↓
message_sending priority 80
  ├── V2.1 读取完整回复
  ├── StickerSendDecider
  ├── Ranker
  └── Delivery Policy
  ↓
message_sending priority 70
  ├── 拆成 2～3 个自然文字气泡
  ├── 微信 sendText
  └── 取消原始整段消息
  ↓
延迟发送已选表情图片
```

这样保证表情评分看到完整回复，子气泡不会重复触发表情决策，文字先到达，表情随后到达。

## 两个仓库的职责

### Yogurt Chat Bot

本仓库负责：

- 微信文字聊天；
- OpenClaw 社交上下文注入；
- 自动记忆与稳定记忆；
- 多气泡与表情发送；
- Mouth / VoiceSession；
- Daily TTS Runtime 接口；
- Singing Pipeline；
- 未来 Native / Live2D / Robot 的能力接口。

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
├── social_layer/                      # 社交、记忆、表情运行时
├── integrations/openclaw/             # 微信/OpenClaw Hook
├── capabilities/
│   ├── mouth/                         # 独立语音合成能力
│   └── voice_session/                 # 队列、优先级、播放与取消
├── voice_orchestrator/                # scene → bounded voice plan
├── voice_runtime/                     # 统一本地 Voice Runtime gateway
├── voice/
│   ├── tts/                           # Yogurt Daily TTS Runtime
│   └── singing/                       # Singing Pipeline V2.1
├── assets/stickers/                   # 本地表情资产
├── scripts/
└── docs/
```

模型权重、参考音频、歌曲、Stem、生成 WAV/MP3、SQLite runtime database 和私有配置不得提交到仓库。

## 前置条件

微信文字端：

- OpenClaw `>=2026.7.1-0`；
- Node.js `>=22.22.3`；
- Python `>=3.9`；
- Ollama；
- 已配置可用聊天模型；
- 已连接 `openclaw-weixin`。

Voice / Singing Runtime 有独立环境与依赖，请分别参考：

```text
capabilities/mouth/README.md
voice/README.md
voice/tts/README.md
voice/singing/README.md
voice_runtime/README.md
```

不要把 GPT-SoVITS、Seed-VC、pymss/RoFormer 等依赖混装进微信聊天 Runtime。

## 微信首次安装

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

首次安装默认保持可选功能关闭：

```json
{
  "stickerEnabled": false,
  "autoMemoryEnabled": false,
  "multiBubbleEnabled": false
}
```

## 已安装微信环境升级

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

## OpenClaw Hook 权限

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

## 启用微信功能

### 表情系统

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

默认自动晋升阈值：`0.78`。

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

修改配置后：

```bash
openclaw config validate
openclaw gateway restart
```

## 微信运行时配置

插件 ID：`yogurt-social-injector`

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

完整示例：`integrations/openclaw/config.example.json`。

## 自动记忆检查

```bash
bash scripts/yogurt-memory --pretty candidates --user-id owner
bash scripts/yogurt-memory --pretty candidates --user-id owner --status pending
bash scripts/yogurt-memory --pretty snapshot --user-id owner
bash scripts/yogurt-memory --pretty revisions --user-id owner --limit 50
```

这些只读检查不会修改数据库。

## 表情评分观察

```bash
cd ~/Yogurt_Chat_Bot
bash scripts/watch-sticker-scores
```

常见日志：

```text
send decision trigger=
send decision rejected by delivery policy
post-reply sticker selected
sticker media sent
sticker media send failed
multi bubble sent
```

常见拒绝原因：

```text
proactive_threshold
score_margin
global_cooldown
candidate_cooldown
hourly_limit
```

## 从 Tagger 部署表情资产

推荐流程：

```text
审核并批准
→ 决策实验室模拟
→ 验证
→ 导出并发布到酸奶
```

Catalog 和 `images/` 必须一起同步。

## 测试

微信 / 社交层：

```bash
bash scripts/test-openclaw-adapter
bash scripts/test-sticker-explicit-fallback
bash scripts/test-sticker-auto-delivery
bash scripts/test-sticker-v21
bash scripts/test-v3
bash scripts/test-v3-runtime
bash scripts/test-v3-sticker-bubble-order
```

Voice / Mouth / Singing：

```bash
python -m pytest -q voice_runtime/tests
python -m pytest -q voice_orchestrator/tests
python -m pytest -q capabilities/mouth capabilities/voice_session
python -m pytest -q voice/singing/tests voice/tts/tests
```

CI 当前覆盖：

- Python 3.9；
- Python 3.10；
- Python 3.11；
- Python 3.12；
- Node.js 22。

## 安全与隐私边界

- 微信不接入 Mouth、TTS 或 Singing；
- 不把密码、Token、验证码、密钥、身份文件和支付信息写入长期记忆；
- 不允许助手回复成为用户事实；
- 不在实时微信聊天中调用 VLM；
- 不向聊天模型暴露表情绝对路径；
- 不向公共接口暴露模型权重、参考音频和私有路径；
- 技术和严肃场景默认不主动发表情；
- 高风险求助场景禁止发表情；
- 图片失败保留文字；
- 自动记忆失败保留聊天；
- 多气泡部分发送失败时避免重复发送整段原文；
- Singing 失败不会影响 Daily TTS；
- Voice Runtime 失败不会影响微信文字聊天。

## 版本演进

微信文字端：

```text
V1    社交上下文、关系和显式记忆
V2    微信真实图片发送链路
V2.1  最终回复后的发送决策、语义 Ranker 和 Delivery Policy
V3    自动记忆候选、修订审计、自然多气泡和表情/气泡协调器
```

Voice / Embodiment：

```text
Daily TTS V1       GPT-SoVITS 80:20 + Emotion Router
Mouth              独立说话能力与安全 API
VoiceSession       队列、优先级、播放与取消
Singing V2.1       Lead-only Seed-VC + 原和声 preserve/remove
Future Ears        STT / VAD / realtime input
Future Body        Live2D / desktop / robot embodiment
Future Eyes        screen / camera / environment vision
```

## 下一阶段方向

微信继续保持文字输出，不把语音链路塞进 OpenClaw/微信发送路径。

下一阶段优先围绕酸奶本体能力继续扩展：

```text
Native / Desktop host
  ↓
Mouth + VoiceSession
  ↓
Live2D lipsync / expression
  ↓
Ears (STT / VAD / interruption)
  ↓
Eyes (screen / camera)
  ↓
Robot / physical embodiment
```

Singing V2.1 和 Daily TTS V1 作为相对稳定的独立 Runtime 保留，后续以音质调优为主，而不是继续改变微信架构。

## License

当前仓库为私人项目，未声明公共开源许可证。
