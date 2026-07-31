# 酸奶表情包资产目录

本目录用于 V2 本地图片表情包系统。

## 本地目录结构

```text
assets/stickers/
  images/                 # 本地图片，不提交到 Git
  stickers.json           # 本地正式标签，不提交到 Git
  stickers.example.json   # 可提交的示例
  stickers.schema.json    # 标签格式约束
```

## 初始化

```bash
cd ~/Yogurt_Chat_Bot
mkdir -p assets/stickers/images
cp assets/stickers/stickers.example.json assets/stickers/stickers.json
```

把图片复制到：

```text
assets/stickers/images/
```

然后在 `stickers.json` 中为每张图填写标签。

## 文件命名建议

使用稳定、可读且不会随排序变化的文件名：

```text
yogurt-happy-001.png
yogurt-comfort-001.jpg
yogurt-confused-001.webp
```

不要使用只有数字的文件名，也不要在后续更新时复用旧 ID 指向另一张图。

## 支持格式

V2.0 计划支持：

- PNG
- JPEG / JPG
- WEBP
- GIF 是否能以预期方式在微信播放，需要单独实机验证；V2.0 不把 GIF 动画作为必需能力

## 隐私和 Git

以下内容默认被 `.gitignore` 排除：

- `images/` 下的真实表情包
- `stickers.json` 本地正式标签
- 自动分类生成的待审核清单
- 缩略图和运行缓存

仓库只保存 Schema、示例和测试夹具。不要提交私人聊天截图、未授权人物图片或含敏感信息的表情包。

## 人工标签原则

- `scenes` 表示适用的 V1 场景
- `emotions` 表示图片表达的主要情绪
- `keywords` 是当前消息中可能触发该图的词
- `relationships` 限制可发送给哪些关系等级
- `source=auto, reviewed=false` 的自动结果不会进入生产候选
- 不确定是否适用时宁可少标，不要让一张图覆盖所有场景

## 自动分类

V2.0 不依赖自动分类。未来离线分类器会读取 `images/`，生成待审核标签建议，但不会直接启用或发送图片。
