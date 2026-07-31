# Yogurt Social Layer V2 表情包系统设计

> 状态：设计启动
> 分支：`feature/sticker-system-v2`
> 基线：V1.0（已归档）

## 1. V2 目标

V2 在 V1 已稳定运行的社交路由基础上加入本地图片表情包能力：

- 本地表情包图片目录
- 独立、可审阅的标签文件
- 根据 V1 场景、消息关键词和用户关系选择表情包
- 全局、场景和单图冷却机制
- 每轮最多发送一张图片
- 微信官方 Bot 图片回复
- 发送失败时不影响原有文本回复
- 可选的离线自动分类和标签建议

V2 不应为了发图而强行匹配。没有可靠候选、处于冷却、图片无效或渠道不支持时，应正常只发送文本。

## 2. 借鉴的开源设计

### MaiBot 文本选择器

值得采用：

- 两阶段选择：先召回少量候选，再做最终决策
- 允许明确返回 `no_match`
- 一轮最多发送一张
- 选择与发送使用同一份资产快照，避免索引变化后发错图
- 发送失败后不随机换图补发

V2 初版不额外调用 LLM，因此把“两阶段”实现为：

1. 规则和标签召回
2. 加权打分、冷却过滤和阈值决策

### MaiBot 风格 AstrBot 表情系统

值得采用：

- 图片文件和元数据分离
- 元数据可持久化、可编辑、可禁用
- 自动描述、情绪标签和向量检索作为可选增强
- 自动生成结果先作为描述文档或标签建议，而不是不可追踪地直接发送

V2 初版使用 JSON 标签清单；运行时冷却状态使用 SQLite。后续图库明显扩大后，再考虑把资产元数据整体迁入 SQLite。

### 腾讯 openclaw-weixin

微信图片发送涉及：

- 原图与缩略图
- 明文大小和 MD5
- AES-128-ECB 加密
- CDN 上传参数
- 构造图片消息并发送

V2 不直接复制腾讯插件的内部发送流程，优先使用 OpenClaw 已规范化的回复媒体载荷，让 `openclaw-weixin` 继续负责具体 CDN 上传和协议细节。

## 3. 总体架构

```text
微信消息
  ↓
before_prompt_build
  ↓
V1 SocialLayerService
  ├── scene
  ├── mood
  └── social_context
  ↓
V2 StickerSelector
  ├── 读取资产快照
  ├── 场景/关键词/关系召回
  ├── 冷却过滤
  ├── 加权评分
  └── 生成 pending sticker decision
  ↓
Qwen 生成文本回复
  ↓
reply_payload_sending
  ├── 根据 runId/sessionKey 找到 pending decision
  └── 给 ReplyPayload 添加媒体引用
  ↓
openclaw-weixin 上传并发送图片
  ↓
message_sent
  └── 成功后提交冷却状态；失败则丢弃 pending decision
```

## 4. 文件和目录

```text
assets/stickers/
  README.md
  images/
    .gitkeep
  stickers.schema.json
  stickers.example.json

social_layer/src/yogurt_social/stickers/
  __init__.py
  models.py
  catalog.py
  cooldown.py
  selector.py

social_layer/tests/
  test_sticker_catalog.py
  test_sticker_cooldown.py
  test_sticker_selector.py

docs/
  V2_DESIGN.md
```

真实私人表情包图片默认不提交到 Git 仓库。仓库只保存目录说明、Schema、示例标签和测试夹具。

## 5. 标签模型

每张图片至少包含：

```json
{
  "id": "yogurt-smile-001",
  "file": "images/yogurt-smile-001.png",
  "enabled": true,
  "scenes": ["casual", "celebration"],
  "emotions": ["happy", "warm"],
  "keywords": ["开心", "好耶", "成功", "谢谢"],
  "tone": ["cute", "gentle"],
  "relationships": ["owner", "close"],
  "weight": 1.0,
  "cooldown_seconds": 1800,
  "description": "开心地举手庆祝，适合成功、感谢和轻松回应",
  "source": "manual",
  "reviewed": true
}
```

约束：

- `id` 全局唯一
- `file` 必须位于配置的资产根目录中，禁止路径穿越
- `enabled=false` 的图片永不参与选择
- `source=auto` 且 `reviewed=false` 的图片默认不参与生产发送
- 标签文件中的路径、格式和枚举在加载时严格校验

## 6. 场景选择

V1 场景直接作为 V2 的主要召回信号：

- `casual`：轻松、可爱、问候
- `technical`：疑惑、认真、修复成功；默认降低发图概率
- `playful`：调侃、得意、坏笑
- `comfort`：安慰、抱抱、陪伴
- `celebration`：开心、鼓掌、庆祝
- `serious`：默认不发，除非标签明确允许且得分很高

候选分数建议：

```text
scene_match       +4
emotion_match     +3
keyword_match     +2 each, capped
relationship      +1
manual_weight     × final score
recent_same_image -∞
recent_any_sticker penalty or block
below_threshold   → no_match
```

同分候选使用加权随机，避免总是发送同一张图；测试时允许注入固定随机种子。

## 7. 冷却机制

冷却状态写入 V1 同一个 SQLite 数据库中的独立表，不修改正式记忆表。

需要三层冷却：

1. **全局冷却**：任意表情发送后，一段时间内不再发送表情。
2. **场景冷却**：相同场景短时间不连续发图。
3. **单图冷却**：同一图片较长时间内不得重复。

建议默认值：

```text
global_cooldown_seconds = 300
scene_cooldown_seconds = 900
per_sticker_cooldown_seconds = 1800
max_stickers_per_hour = 4
```

只在 `message_sent` 确认投递成功后提交冷却；模型失败、媒体上传失败或回复被取消时不得消耗冷却。

## 8. 微信图片发送策略

优先方案：在 `reply_payload_sending` 中向规范化 `ReplyPayload` 添加媒体路径或媒体 URL，由 OpenClaw 和 `openclaw-weixin` 负责平台发送。

注意：OpenClaw 的消息 hook 不能自行授予本地媒体信任，因此正式实现前必须通过实机最小验证确认：

- 链接插件目录下的本地图片是否可直接作为媒体引用
- 是否必须把图片复制到 OpenClaw 允许的媒体目录
- 微信是否发送为独立图片消息或带文字说明
- 文本成功但图片失败时的投递结果和 `message_sent` 事件结构

如果规范化载荷无法安全附加本地文件，第二方案是在 Yogurt 插件中注册一个窄权限 `send_sticker` 工具，调用 OpenClaw 公开消息发送能力；不直接依赖腾讯插件内部未承诺稳定的函数。

## 9. 自动分类：建议暂定为 V2.1 可选离线工具

结论：**需要保留设计入口，但不要作为 V2.0 的上线依赖。**

原因：

- 当前图库预计先以人工挑选的小规模资产为主，手工标签成本可控
- 自动分类需要 VLM；当前主聊天模型不应被迫承担额外图片分析调用
- 自动标签可能误判语气、文字梗、人物关系和不适合发送的内容
- 自动分类若直接进入生产发送，会降低可控性和可解释性

推荐方式：

```text
导入图片
  ↓
离线分类命令
  ↓
生成 description / scenes / emotions / keywords 建议
  ↓
写入 source=auto, reviewed=false
  ↓
人工审核
  ↓
reviewed=true 后进入生产候选
```

自动分类可以分两级：

1. **低成本规则预分类**：文件名、目录名、已有 sidecar 文本。
2. **可选 VLM 分类**：生成画面描述、OCR 文本、情绪、适用场景和安全标签。

VLM 不应直接决定最终发送；它只生成可审核的元数据。

## 10. V2.0 开发顺序

### Milestone A：资产与选择核心

- 图片目录和 `.gitignore`
- JSON Schema 与示例标签
- Catalog 严格加载
- 场景、关键词和关系加权选择
- `no_match`
- 单元测试

### Milestone B：冷却

- SQLite 冷却表
- 全局、场景、单图和每小时上限
- pending / commit / rollback 生命周期
- 并发安全测试

### Milestone C：OpenClaw 集成

- V1 bridge 返回场景与 sticker decision
- `runId/sessionKey` pending decision 缓存
- `reply_payload_sending` 添加媒体
- `message_sent` 提交或回滚冷却
- 插件开关和 fail-open

### Milestone D：微信实机验收

- 文本 + 图片正常发送
- 图片失败不阻断文本
- 冷却生效
- 严肃场景不乱发图
- 无匹配时只发文本
- 连续多轮不会重复同一张

### V2.1 候选

- 离线 VLM 自动分类
- OCR 和敏感内容审核
- 管理页或批量审核 CLI
- Embedding 召回
- 从聊天图片中受控采集表情包

## 11. V2 验收标准

V2.0 只有在以下条件全部满足时才可合并到 `main`：

- 资产清单加载失败时 fail-open
- 每轮最多发送一张表情包
- 无可靠匹配时不发送
- 冷却仅在成功投递后提交
- 图片路径无法越出资产目录
- 私人图片和运行时数据库不进入 Git
- 微信连续实机测试中，文本与图片均成功送达
- 禁用 V2 后 V1 文本社交层完全不受影响
