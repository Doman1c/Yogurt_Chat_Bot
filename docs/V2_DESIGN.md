# Yogurt Social Layer V2 表情包系统设计

> 状态：架构确认 / 开发启动  
> 分支：`feature/sticker-system-v2`  
> 基线：V1.0（已归档）

## 1. 架构结论

V2 采用两个彼此独立的程序：

1. **Sticker Tagger（离线表情资产处理器）**
   - 接收上传的表情包或普通照片
   - 自动识别画面、OCR 文字、情绪和适用场景
   - 生成结构化标签与描述
   - 整理到可供聊天机器人使用的正式图片库
   - 不连接微信，不参与每轮聊天，不直接发送图片

2. **Sticker Runtime（聊天运行时）**
   - 只读取已经分类完成的图片库
   - 根据 V1 场景、情绪、当前消息和冷却状态召回少量候选
   - 把候选 ID 和文字描述提供给当前主聊天模型
   - 主聊天模型自主决定是否选择一张表情包
   - 后端验证选择、执行冷却并发送到微信

自动分类和聊天选择必须解耦。聊天时不临时调用视觉模型看图，也不重新生成标签。

## 2. 完整数据流

### 2.1 离线分类流程

```text
用户上传图片到 assets/stickers/inbox/
  ↓
Sticker Tagger 扫描新文件
  ├── 文件格式和大小检查
  ├── SHA-256 去重
  ├── 图片归一化与缩略图
  ├── VLM 画面理解
  ├── OCR 文字提取
  ├── 场景、情绪、语气和关键词分类
  └── 安全与适用边界判断
  ↓
生成结构化记录
  ↓
复制/移动到 assets/stickers/images/
  ↓
更新 stickers.json 或 SQLite catalog
```

### 2.2 聊天选择流程

```text
微信消息
  ↓
V1 SocialLayerService
  ├── scene
  ├── mood
  ├── relationship
  └── social_context
  ↓
Sticker Runtime
  ├── 读取已分类目录
  ├── 按 scene/mood/关键词召回
  ├── 冷却和安全过滤
  └── 输出 Top-K 候选快照
  ↓
before_prompt_build 注入候选元数据
  ↓
当前 Qwen 主模型生成回复
  ├── 可以不选择图片
  └── 可调用 choose_sticker(sticker_id)
  ↓
后端验证 sticker_id 属于本轮候选
  ↓
reply_payload_sending 附加图片
  ↓
openclaw-weixin 发送文本和图片
  ↓
message_sent 成功后提交冷却
```

## 3. 关键原则

### 3.1 分类程序独立运行

Sticker Tagger 不依赖 OpenClaw Gateway 是否在线，可以：

- 手动执行一次扫描
- 批量处理一个目录
- 以 watch 模式监听新图片
- 未来增加独立管理页面

分类失败只影响该图片入库，不影响聊天机器人。

### 3.2 主模型自主选择，但后端拥有最终决定权

模型可以根据回复语境决定：

- 不发送表情包
- 从本轮候选中选择一张

模型不能：

- 选择未进入本轮候选快照的 ID
- 绕过冷却机制
- 发送被禁用、未审核或存在风险的图片
- 直接指定任意本地文件路径

后端始终重新验证模型选择。验证失败时只发送文本。

### 3.3 不增加第二次聊天 LLM 调用

主聊天模型在原本生成回复的同一轮中选择表情包。运行时不会再额外调用一个文本模型做选择。

VLM 只在离线 Sticker Tagger 中使用，和实时聊天延迟解耦。

### 3.4 不强制发图

没有可靠候选、处于冷却、模型不想发、图片发送失败时，均正常退化为纯文本回复。

## 4. 目录结构

```text
assets/stickers/
  README.md
  inbox/                         # 新上传、待处理图片
  images/                        # 已归一化的正式图片库
  thumbnails/                    # 本地管理预览图
  stickers.schema.json           # 正式标签 Schema
  stickers.example.json          # 示例记录
  stickers.json                  # 本地正式目录，默认不提交 Git

runtime/sticker_tagger/
  jobs.sqlite3                   # 分类任务与失败记录
  cache/                         # VLM/OCR 缓存
  rejected/                      # 无效或拒绝图片

tools/sticker_tagger/
  README.md
  config.example.json
  src/yogurt_sticker_tagger/
    cli.py
    scanner.py
    normalizer.py
    classifier.py
    providers.py
    catalog_writer.py

social_layer/src/yogurt_social/stickers/
  catalog.py                     # 正式目录加载与快照
  retriever.py                   # Top-K 候选召回
  cooldown.py                    # 冷却与发送记录
  pending.py                     # runId/sessionKey 待发送选择
```

真实图片、正式 `stickers.json`、任务数据库、缩略图和模型缓存默认不提交 Git。

## 5. Sticker Tagger 输出模型

每张图片的正式记录建议包含：

```json
{
  "id": "yogurt-celebrate-001",
  "file": "images/yogurt-celebrate-001.webp",
  "sha256": "...",
  "enabled": true,
  "status": "approved",
  "scenes": ["celebration", "casual"],
  "emotions": ["happy", "warm"],
  "keywords": ["成功", "完成", "好耶", "太好了"],
  "tone": ["cute", "gentle"],
  "relationships": ["owner", "close"],
  "description": "角色开心举手庆祝，适合任务完成或收到好消息时使用",
  "ocr_text": "好耶",
  "content_warning": ["none"],
  "weight": 1.0,
  "cooldown_seconds": 1800,
  "classification": {
    "provider": "local-vlm",
    "model": "configured-model",
    "confidence": 0.91,
    "classified_at": "2026-07-31T23:00:00+08:00"
  },
  "source": "auto",
  "reviewed": false
}
```

### 状态字段

- `pending`：已分类，尚未批准进入正式运行库
- `approved`：可参与聊天候选召回
- `rejected`：不参与运行时选择
- `disabled`：保留记录但暂时禁用

### 审核策略

支持两种模式：

1. `review_required=true`
   - 自动分类后进入 `pending`
   - 人工确认后才变为 `approved`

2. `auto_approve_threshold=0.92`
   - 高置信度、无风险标签的图片可自动批准
   - 低置信度或检测到风险时仍进入 `pending`

初期默认使用人工审核模式，后续可根据误标率开启高置信度自动批准。

## 6. 自动分类任务

Sticker Tagger 至少生成：

- 一句话画面描述
- OCR 文本
- 主情绪与辅助情绪
- 适用聊天场景
- 语气标签
- 关键词
- 是否适合 owner/close/familiar/new 用户
- 内容风险标签
- 分类置信度

### 分类 Provider

程序通过统一 Provider 接口支持：

- 本地 Ollama VLM
- OpenAI 兼容视觉接口
- 其他本地或远程 VLM
- 无 VLM 时的文件名/目录规则降级

分类 Provider 与聊天模型配置完全独立。

### 严格结构化输出

VLM 必须返回满足 Schema 的 JSON。程序负责：

- JSON 解析
- 枚举校验
- 标签数量限制
- OCR 长度限制
- 危险路径过滤
- 无效结果重试或进入失败队列

VLM 不能直接写正式目录文件。

## 7. 聊天运行时候选检索

聊天模型不应收到整个图库。运行时先召回 `3–8` 张候选，只注入：

```text
[Available Stickers]
- id: yogurt-celebrate-001
  description: 开心举手庆祝，适合完成任务或收到好消息
  emotions: happy, warm
- id: yogurt-proud-003
  description: 得意但不过分嘲讽，适合轻松展示成功
  emotions: playful, proud

可以不使用表情包。需要使用时只能调用 choose_sticker，并传入以上 ID。
```

召回信号：

```text
scene_match       +4
mood_match        +3
keyword_match     +2 each（设上限）
relationship      +1
manual_weight     × final score
cooldown_block    → 排除
unsafe_or_pending → 排除
below_threshold   → no candidates
```

候选快照与 `runId/sessionKey` 绑定，防止目录热更新后模型 ID 指向错误图片。

## 8. 模型选择接口

V2 混合插件注册一个 Agent 工具：

```text
choose_sticker(sticker_id)
```

工具行为：

1. 获取当前 `runId/sessionKey`
2. 验证 `sticker_id` 属于本轮候选快照
3. 再次检查状态、路径和冷却
4. 记录 pending decision
5. 返回简短结果，让模型继续完成文本回复

模型不调用工具即代表本轮不发图。

OpenClaw 工具策略必须允许 `choose_sticker`，否则模型不会收到工具 Schema。

## 9. 冷却机制

冷却由后端强制执行，不依赖模型自觉：

```text
global_cooldown_seconds = 300
scene_cooldown_seconds = 900
per_sticker_cooldown_seconds = 1800
max_stickers_per_hour = 4
```

流程：

```text
choose_sticker
  ↓
pending（不消耗冷却）
  ↓
reply_payload_sending
  ↓
message_sent success
  ↓
commit cooldown
```

任何失败或取消均清理 pending，不消耗冷却。

## 10. 微信图片发送

优先通过 OpenClaw 规范化 `ReplyPayload` 和媒体引用交给 `openclaw-weixin`。腾讯插件继续负责缩略图、加密、CDN 上传和图片消息协议。

正式实现前需要做最小实机验证：

- 插件能否安全引用 `assets/stickers/images/` 下的本地文件
- 是否需要复制到 OpenClaw 的可信媒体目录
- 文本和图片的发送顺序
- 图片失败时 `message_sent` 的结果结构

由于 `reply_payload_sending` 无法自行授予本地媒体信任，若本地媒体路径无法通过规范化载荷发送，则改用 OpenClaw 公开的发送能力或一个受限的发送服务；不直接依赖腾讯插件未承诺稳定的内部函数。

## 11. 参考项目中采用的思想

### MaiBot 文本选择器

采用：

- 候选召回后再由主模型选择
- 允许 `no_match`
- 每轮最多一张
- 同一候选快照完成选择和发送
- 发送失败不随机换图

### AstrBot MaiBot 风格表情系统

采用：

- 图片与元数据分离
- 独立自动识别和描述
- 可审核的分类结果
- 图片状态、禁用和风险标签
- 后续可扩展 Embedding 检索

不采用其“聊天时额外调用选择模型”的部分；酸奶优先让现有主模型在同一轮中完成选择。

## 12. 开发里程碑

### Milestone A：独立 Sticker Tagger

- CLI 和配置
- inbox 扫描
- SHA-256 去重
- 图片归一化
- Provider 接口
- VLM 结构化分类
- catalog 写入
- 失败队列和 dry-run
- 单元测试

### Milestone B：运行时目录与候选召回

- Catalog 快照
- 只加载 approved 记录
- 场景/情绪/关键词召回
- Top-K 候选
- `no candidates`
- 候选快照绑定 runId/sessionKey

### Milestone C：模型自主选择

- `before_prompt_build` 候选注入
- `choose_sticker` Agent 工具
- 工具白名单配置
- pending decision
- 无效 ID 和越权选择测试

### Milestone D：冷却与微信发送

- SQLite 冷却
- `reply_payload_sending`
- 本地媒体信任验证
- `message_sent` commit/rollback
- 微信实机发送
- 发送失败保持文本回复

### Milestone E：管理体验

- watch 模式
- pending 审核命令
- 批量启用/禁用
- 重分类
- 统计与误标记录
- 可选本地 Web 管理页面

## 13. V2 验收条件

- 新图片可通过独立程序自动生成结构化标签
- 分类程序关闭或故障不影响微信聊天
- 运行时只读取 approved 图片
- 主模型可以不选图，也能从本轮候选中选择一张
- 模型不能选择候选以外的图片
- 冷却机制后端强制生效
- 图片成功发送至微信
- 图片发送失败时文本仍正常送达
- 每轮不超过一张图片
- 不增加第二次实时聊天 LLM 调用
- V1 的原始消息保持和 fail-open 不变量继续成立
