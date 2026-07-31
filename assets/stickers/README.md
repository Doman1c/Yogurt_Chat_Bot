# 酸奶表情包资产目录

本目录保存 V2 的待分类图片、正式图片库和结构化标签。

自动分类由独立的 `Yogurt Sticker Tagger` 完成；OpenClaw 聊天运行时只读取已经分类完成并允许使用的图片。

## 本地目录结构

```text
assets/stickers/
  inbox/                  # 新上传、尚未处理的图片
  images/                 # 已归一化的正式图片库
  thumbnails/             # 管理和审核用缩略图
  stickers.json           # 本地正式目录，不提交 Git
  stickers.example.json   # 可提交的示例
  stickers.schema.json    # 标签格式约束
```

## 使用流程

### 1. 上传图片

把新的表情包或照片放入：

```text
assets/stickers/inbox/
```

### 2. 运行独立分类程序

```bash
python3 -m yogurt_sticker_tagger scan
```

分类程序负责：

- 文件检查
- SHA-256 去重
- 图片归一化
- 视觉理解
- OCR
- 场景、情绪、语气和关键词标签
- 风险标签
- 写入 `stickers.json`

成功处理的图片会进入：

```text
assets/stickers/images/
```

### 3. 聊天运行时读取

聊天系统不会重新分析图片，只会读取 `stickers.json` 中已经批准的记录，召回少量候选交给主聊天模型选择。

## 初始化

```bash
cd ~/Yogurt_Chat_Bot
mkdir -p \
  assets/stickers/inbox \
  assets/stickers/images \
  assets/stickers/thumbnails

cp \
  assets/stickers/stickers.example.json \
  assets/stickers/stickers.json
```

## 文件命名

原始上传文件名可以任意。Sticker Tagger 入库时应生成稳定名称，例如：

```text
yogurt-happy-001.webp
yogurt-comfort-001.webp
yogurt-confused-001.webp
```

不要复用旧 ID 指向另一张图片。正式记录使用 SHA-256 检测重复内容。

## 支持格式

V2.0 计划支持：

- PNG
- JPEG / JPG
- WEBP
- GIF 可先作为静态首帧分类；是否向微信发送动画需要单独实机验证

## 审核模式

分类器支持两种发布策略：

- `review_required=true`：分类后先进入 `pending`
- 高置信度自动批准：满足阈值且无风险标签时进入 `approved`

聊天运行时只使用状态为 `approved` 且 `enabled=true` 的记录。

## 标签含义

- `scenes`：适用的 V1 聊天场景
- `emotions`：图片表达的主要情绪
- `keywords`：与图片语义相关的触发词
- `tone`：可爱、温柔、调皮、认真等表达气质
- `relationships`：允许面向的关系等级
- `description`：供主聊天模型理解图片用途的简短描述
- `ocr_text`：图片内识别出的文字
- `content_warning`：敏感或不适用边界
- `classification`：分类模型、时间和置信度

## 隐私和 Git

以下内容默认由 `.gitignore` 排除：

- `inbox/` 中的真实上传图片
- `images/` 中的真实表情包
- `thumbnails/`
- `stickers.json`
- 自动分类任务数据库和缓存

仓库只保存 Schema、示例、说明和测试夹具。不要提交私人聊天截图、未授权人物图片、敏感证件或含私人信息的照片。

## 相关文档

- `docs/V2_DESIGN.md`
- `tools/sticker_tagger/README.md`
- `stickers.schema.json`
