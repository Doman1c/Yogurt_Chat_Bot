# Yogurt Sticker Tagger 集成说明

V2 的离线图片分类器已经拆分为独立仓库：

```text
Doman1c/Yogurt_Sticker_Tagger
```

本目录不再保存第二套分类器源码或配置，避免 Chat Bot 和 Tagger 的处理逻辑、Schema 与模型参数发生漂移。

## 职责边界

### Yogurt Sticker Tagger

负责：

- 扫描原始图片；
- SHA-256 去重；
- 图片归一化与缩略图；
- Ollama 视觉分类与 OCR；
- 严格 Schema 校验；
- 人工审核；
- 导出 `schema_version: "1.0"` 的 `stickers.json`。

不负责：

- 读取微信消息；
- 判断某轮是否发送表情；
- 冷却；
- 微信图片上传与发送。

### Yogurt Chat Bot

负责：

- 读取 Tagger 导出的 Catalog；
- 根据 V1 场景、情绪、关系和当前消息召回候选；
- 后续由主模型在本轮候选中决定是否选择；
- 后端再次校验选择；
- 强制执行冷却；
- 通过 OpenClaw 微信链路发送。

## Tagger 当前使用方式

```bash
cd ~/Yogurt_Sticker_Tagger
source .venv/bin/activate

yogurt-sticker-tagger scan
yogurt-sticker-tagger review list --status pending
yogurt-sticker-tagger review approve <sticker-id>
yogurt-sticker-tagger export
```

Tagger 的配置格式、Provider 参数和故障排查说明以独立仓库为准。

## 稳定接口

两个项目之间唯一必须稳定的接口是：

```text
Yogurt_Sticker_Tagger/data/stickers.json
```

权威 Schema：

```text
Yogurt_Sticker_Tagger/schemas/sticker-catalog-v1.schema.json
```

Chat Bot 不再维护重复的 `stickers.schema.json`。

## 本地同步

将 Tagger 导出的 Catalog 和正式图片同步到：

```text
Yogurt_Chat_Bot/assets/stickers/stickers.json
Yogurt_Chat_Bot/assets/stickers/images/
```

这些真实运行资产默认不会提交 Git。
