# 酸奶表情包资产目录

本目录是 Yogurt Chat Bot V2 的本地表情资产入口。

自动分类由独立仓库 `Doman1c/Yogurt_Sticker_Tagger` 完成；Chat Bot 不维护第二套分类 Schema，也不会在聊天时重新调用视觉模型分析图片。

## 唯一 Catalog 契约

权威 Schema 位于：

```text
Yogurt_Sticker_Tagger/schemas/sticker-catalog-v1.schema.json
```

Chat Bot 直接消费 Tagger 导出的 `schema_version: "1.0"` Catalog。字段名称必须保持一致，例如：

```text
tones
content_warnings
status
confidence
classification
```

不要在 Chat Bot 中重新引入 `tone`、`content_warning`、`reviewed` 或内嵌运行设置等旧字段。

## 本地目录结构

```text
assets/stickers/
├── inbox/                  # 可选：准备交给 Tagger 的原始图片
├── images/                 # Tagger 导出的正式图片
├── thumbnails/             # 本地审核缩略图
├── stickers.json           # Tagger 导出的正式 Catalog，不提交 Git
├── stickers.example.json   # 脱敏格式示例
└── README.md
```

真实图片、缩略图和 `stickers.json` 都由 `.gitignore` 排除。

## 推荐工作流

### 1. 在 Sticker Tagger 中处理图片

```bash
cd ~/Yogurt_Sticker_Tagger
source .venv/bin/activate

yogurt-sticker-tagger scan
yogurt-sticker-tagger review list --status pending
yogurt-sticker-tagger review approve <sticker-id>
yogurt-sticker-tagger export
```

### 2. 将导出结果同步到 Chat Bot

Tagger 默认把 `file` 写成相对路径，例如：

```text
images/sticker-0123456789ab.webp
```

因此需要让 Catalog 和对应的 `images/` 位于同一个资产根目录。同步时复制：

```text
Yogurt_Sticker_Tagger/data/stickers.json
  → Yogurt_Chat_Bot/assets/stickers/stickers.json

Yogurt_Sticker_Tagger/data/images/
  → Yogurt_Chat_Bot/assets/stickers/images/
```

缩略图只用于管理界面，不是聊天运行时必需项。

### 3. 配置 Chat Bot 运行时

复制独立运行配置：

```bash
cp social_layer/config/stickers.example.json \
   social_layer/config/stickers.json
```

运行配置保存候选阈值、候选数量以及未来的冷却参数，不写入 `stickers.json`。

初期保持：

```json
{
  "enabled": false
}
```

直到候选召回、模型选择工具和微信发送全部完成并通过实机测试。

## 当前运行时能力

`social_layer/src/yogurt_social/stickers/` 已提供：

- `StickerCatalogLoader`：读取 Tagger Catalog；
- 路径逃逸与缺失文件检查；
- `approved + enabled` 防御性过滤；
- 场景、情绪、关键词和关系评分；
- 风险标签过滤；
- Top-K 候选输出；
- 独立运行配置加载。

当前尚未接入：

- OpenClaw `before_prompt_build` 候选注入；
- `choose_sticker` 工具；
- SQLite 冷却；
- 微信本地图片发送；
- 发送成功后的冷却提交。

## 安全约束

运行时只接受：

- `schema_version = "1.0"`；
- `images/` 下的相对图片路径；
- 支持的 PNG、JPEG 或 WEBP 文件；
- 合法稳定 ID；
- 已批准、已启用、无风险的候选。

模型永远不能指定任意本地绝对路径。
