# Yogurt Social Layer V2 表情包系统设计

> 状态：Milestone A 完成，Milestone B 基础模块已实现  
> 分支：`feature/sticker-system-v2`  
> 基线：V1.0（已归档）

## 1. 架构结论

V2 由两个独立项目组成：

1. **Yogurt Sticker Tagger**
   - 离线扫描、去重和归一化图片；
   - 调用视觉模型生成描述、OCR 和标签；
   - 完成人工审核；
   - 导出稳定 Catalog；
   - 不连接微信，不参与实时聊天。

2. **Yogurt Chat Bot Sticker Runtime**
   - 只读取 Tagger 已导出的 Catalog；
   - 使用 V1 的场景、情绪和关系状态召回候选；
   - 后续让当前主模型决定是否选择一张；
   - 后端负责安全校验、冷却与微信发送。

离线分类和实时选择必须保持解耦。聊天期间不临时调用视觉模型，也不增加第二次聊天模型调用。

## 2. 唯一 Catalog 契约

权威 Schema 位于独立仓库：

```text
Doman1c/Yogurt_Sticker_Tagger
└── schemas/sticker-catalog-v1.schema.json
```

Chat Bot 不维护第二套图片 Schema。它直接消费 Tagger 导出的：

```json
{
  "schema_version": "1.0",
  "generated_at": "...",
  "generated_by": "Yogurt Sticker Tagger",
  "asset_root": ".",
  "stickers": []
}
```

关键字段保持 Tagger 命名：

```text
tones
content_warnings
status
confidence
classification
```

以下旧字段不再使用：

```text
tone
content_warning
reviewed
version
settings
```

## 3. 运行设置与资产数据分离

`stickers.json` 只保存图片资产及分类结果。

运行策略保存在：

```text
social_layer/config/stickers.json
```

示例：

```json
{
  "schema_version": "1.0",
  "enabled": false,
  "catalog_path": "assets/stickers/stickers.json",
  "minimum_score": 5.0,
  "max_candidates": 5,
  "require_files": true,
  "global_cooldown_seconds": 300,
  "scene_cooldown_seconds": 900,
  "default_sticker_cooldown_seconds": 1800,
  "max_stickers_per_hour": 4,
  "allowed_channels": ["ilink"]
}
```

在微信发送链路完成前，默认保持 `enabled=false`。

## 4. 数据流

### 4.1 离线分类

```text
图片进入 Yogurt_Sticker_Tagger/data/inbox/
  ↓
格式检查与 SHA-256 去重
  ↓
WEBP 归一化与缩略图
  ↓
Ollama 视觉分类与 OCR
  ↓
严格 Schema 校验
  ↓
pending 人工审核
  ↓
approved + enabled
  ↓
导出 data/stickers.json
```

### 4.2 资产同步

```text
Yogurt_Sticker_Tagger/data/stickers.json
  → Yogurt_Chat_Bot/assets/stickers/stickers.json

Yogurt_Sticker_Tagger/data/images/
  → Yogurt_Chat_Bot/assets/stickers/images/
```

Tagger 导出的 `asset_root` 默认为 `.`，因此 Catalog 和 `images/` 必须保持同一个资产根目录。

### 4.3 候选召回

```text
V1 DialogueState
  ├── scene
  ├── mood
  ├── relationship
  └── use_sticker
        ↓
StickerRuntime
  ├── 加载 Catalog 快照
  ├── 验证相对路径
  ├── 过滤未批准、禁用和风险图片
  ├── 场景/情绪/关键词/关系评分
  └── 输出 Top-K 候选
```

### 4.4 后续完整发送链路

```text
Top-K 候选注入 before_prompt_build
  ↓
主模型可以不选择，或调用 choose_sticker(sticker_id)
  ↓
后端验证 ID 属于本轮候选快照
  ↓
检查冷却和渠道权限
  ↓
reply_payload_sending 附加本地图片
  ↓
openclaw-weixin 上传并发送
  ↓
message_sent 成功后提交冷却
```

## 5. 当前已实现模块

```text
social_layer/src/yogurt_social/stickers/
├── __init__.py
├── models.py
├── config.py
├── catalog.py
├── retriever.py
└── runtime.py
```

### 5.1 `StickerCatalogLoader`

当前负责：

- 读取 `schema_version: "1.0"` Catalog；
- 检查根节点、必需字段和字段类型；
- 检查稳定 ID 与 SHA-256；
- 检查场景、关系和风险枚举；
- 只允许 `images/` 下的相对路径；
- 阻止 `../` 路径逃逸；
- 可选检查图片文件是否真实存在；
- 检查重复 sticker ID；
- 生成不可变 Catalog 快照。

它不会重新实现 Tagger 的完整 JSON Schema，而只做运行时必须的防御性校验。Schema 的唯一权威来源仍是 Tagger。

### 5.2 `StickerRuntimeConfig`

当前负责：

- 读取独立运行设置；
- 检查阈值和范围；
- 解析相对或绝对 Catalog 路径；
- 在 `enabled=false` 时不要求 Catalog 存在。

### 5.3 `StickerRetriever`

当前评分：

```text
scene match         +4
mood match          +3
keyword match       +2 each，最多两个
relationship match  +1
最终得分            × sticker.weight
```

硬过滤：

- `status != approved`；
- `enabled != true`；
- `content_warnings != ["none"]`；
- 当前关系不在允许关系中；
- V1 `use_sticker=false`；
- 得分低于 `minimum_score`。

排序方式：

```text
score 降序
→ sticker_id 升序作为稳定并列规则
→ 截取 max_candidates
```

### 5.4 `StickerRuntime`

统一入口负责：

- 读取运行配置；
- 在启用时加载 Catalog；
- 构造 Retriever；
- 对外提供单一 `retrieve(...)` 方法；
- 禁用时安全返回空候选。

## 6. 当前尚未实现

以下内容仍未进入正式运行链路：

- `SocialLayerService` 中的候选注入；
- OpenClaw 配置项与安装脚本；
- `before_prompt_build` 的候选文本块；
- `choose_sticker(sticker_id)` Agent 工具；
- 候选快照与 `runId/sessionKey` 绑定；
- SQLite 冷却记录；
- pending decision；
- `reply_payload_sending` 图片附加；
- `message_sent` 成功提交或失败回滚；
- 微信本地媒体路径信任验证；
- 实机图片发送。

因此当前 V2 分支不能宣称已经可以自动向微信发送表情包。

## 7. 安全原则

### 7.1 模型不能指定路径

模型未来只能选择本轮候选中的 `sticker_id`。本地路径由后端从 Catalog 解析，模型不能提交任意路径。

### 7.2 Catalog 热更新使用快照

每轮候选必须绑定一个不可变 Catalog 快照。即使本地 `stickers.json` 在生成回复期间更新，也不能让相同 ID 指向另一张图片。

### 7.3 风险图片默认拒绝

当前运行时只允许：

```json
{
  "content_warnings": ["none"]
}
```

任何其他风险标签都不会进入候选。

### 7.4 fail-open

未来 V2 任一模块异常时：

```text
Catalog 读取失败
候选召回失败
工具选择失败
冷却数据库失败
图片上传失败
```

都应退化为原来的纯文本回复，不能阻断微信消息。

## 8. 冷却设计

运行配置已经预留：

```text
global_cooldown_seconds
a scene_cooldown_seconds
default_sticker_cooldown_seconds
max_stickers_per_hour
```

正式实现时遵循：

```text
choose_sticker
  ↓
pending，不消耗冷却
  ↓
图片实际发送成功
  ↓
message_sent commit
```

发送失败、超时或取消时清理 pending，不消耗冷却，也不自动随机换另一张图。

## 9. 微信发送设计

优先使用 OpenClaw 公开的 `ReplyPayload` 与媒体引用能力，让 `openclaw-weixin` 继续负责：

- 本地媒体读取；
- 缩略图；
- 加密与 CDN 上传；
- 微信图片消息协议。

正式实现前必须完成最小实机验证：

1. 插件能否安全引用 `assets/stickers/images/`；
2. 是否需要复制到 OpenClaw 可信媒体目录；
3. 文本和图片发送顺序；
4. 图片失败时 hook 返回结构；
5. `message_sent` 是否能区分文本成功和图片成功。

不得依赖腾讯插件未承诺稳定的内部私有函数。

## 10. 测试

当前新增测试覆盖：

- 运行配置与 Catalog 分离；
- Tagger Catalog 字段兼容；
- `tones` 与 `content_warnings` 正确读取；
- 路径逃逸拒绝；
- pending、风险和关系不匹配过滤；
- 场景候选评分；
- `use_sticker=false` 时返回空；
- V2 禁用时不要求 Catalog 存在。

运行：

```bash
cd ~/Yogurt_Chat_Bot
bash scripts/test-openclaw-adapter
```

## 11. 开发里程碑

### Milestone A：独立 Sticker Tagger

状态：**基本完成并通过真实 Ollama 图片分类。**

已完成：

- CLI 和配置；
- 扫描与去重；
- 图片归一化；
- Ollama VLM；
- 结构化分类；
- SQLite；
- 审核；
- 原子导出；
- 测试。

### Milestone B：Catalog 与候选召回

状态：**基础模块已实现，尚未接入 V1 Service。**

已完成：

- 单一 Catalog 契约；
- 独立运行设置；
- Catalog Loader；
- 路径安全；
- 场景/情绪/关键词/关系召回；
- Top-K；
- 单元测试。

待完成：

- SocialLayerService 接入；
- 候选上下文格式；
- Catalog 热更新策略；
- run/session 快照绑定。

### Milestone C：模型自主选择

状态：**未开始。**

- `choose_sticker`；
- 工具白名单；
- pending decision；
- 无效 ID 与越权测试。

### Milestone D：冷却与微信发送

状态：**未开始。**

- SQLite 冷却；
- 发送前验证；
- ReplyPayload 媒体；
- commit/rollback；
- 微信实机发送。

### Milestone E：管理体验

状态：**部分完成。**

Tagger 已有审核和导出 CLI；watch、批量管理和本地 Web 页面仍属于后续增强。

## 12. 下一开发步骤

按照以下顺序继续：

1. 将 `StickerRuntime` 以可选依赖方式接入 `SocialLayerService`；
2. 在 Python bridge 输出脱敏候选；
3. 在 `before_prompt_build` 注入最多 3–5 个候选；
4. 建立候选快照 ID；
5. 注册 `choose_sticker` 工具；
6. 实现 pending 与 SQLite 冷却；
7. 验证 OpenClaw 本地媒体发送；
8. 完成微信实机测试；
9. 只有全部通过后，才把运行配置改为 `enabled=true`。

## 13. V2 验收条件

V2 完成需要同时满足：

- Tagger 可以稳定导出 Catalog；
- Chat Bot 可以加载同一契约，无字段转换层；
- 只召回 approved、enabled、无风险图片；
- 主模型可以明确选择“不发送”；
- 只能选择本轮候选 ID；
- 冷却由后端强制执行；
- 图片发送成功后才提交冷却；
- 图片失败仍正常发送文本；
- 微信协议和 V1 纯文本链路保持兼容；
- V2 可以通过配置整体关闭；
- 私人图片、Catalog 和运行数据库不会进入 Git。
