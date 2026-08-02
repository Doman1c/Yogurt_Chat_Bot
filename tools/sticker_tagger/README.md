# Yogurt Sticker Tagger 集成说明

V2 的离线图片分类器已经拆分为独立仓库：

```text
Doman1c/Yogurt_Sticker_Tagger
```

本目录不保存第二套分类器源码、Schema 或 Provider 配置，避免 Chat Bot 与 Tagger 发生实现漂移。

## 当前版本与验收状态

```text
Yogurt Sticker Tagger: 0.2.0
Yogurt Chat Bot plugin: 0.3.3
Catalog schema: 1.0
当前微信实机资产基线: 14 张 approved + enabled
```

Tagger 与 Chat Bot 的完整链路已于 2026-08-02 完成微信实机验收。

## 职责边界

### Yogurt Sticker Tagger

负责：

- 扫描 PNG、JPG/JPEG 和 WEBP；
- SHA-256 去重与稳定 Sticker ID；
- WebP 归一化和缩略图；
- Ollama 视觉分类与 OCR；
- Prompt 1.1 中文场景和关键词策略；
- `content_warnings` 规范化；
- pending / approved / rejected 审核；
- enable / disable；
- 受约束标签编辑；
- 重分类；
- SQLite 审计记录；
- 本地或令牌保护的远程 Web Admin；
- 原子导出 `schema_version: "1.0"` Catalog。

不负责：

- 读取微信消息；
- 决定某轮是否使用表情；
- 聊天冷却；
- 微信 CDN 上传；
- 发送聊天消息。

### Yogurt Chat Bot

负责：

- 读取 Tagger 导出的 Catalog；
- 执行 approved、enabled、warning、关系和文件过滤；
- 根据场景、情绪、关键词和关系召回候选；
- auto 模式在本地选择 Top 1；
- tool 模式让主模型调试选择；
- 强制冷却和每小时上限；
- 禁止模型描述或虚构图片；
- 校验真实文件路径和大小；
- 通过 OpenClaw 微信 outbound adapter 发送图片。

## Tagger CLI 工作流

`--config` 是根参数，必须放在子命令前：

```bash
cd ~/Yogurt_Sticker_Tagger
source .venv/bin/activate

yogurt-sticker-tagger --config config.toml doctor
yogurt-sticker-tagger --config config.toml scan --dry-run
yogurt-sticker-tagger --config config.toml scan
yogurt-sticker-tagger --config config.toml status
yogurt-sticker-tagger --config config.toml review list --status pending
yogurt-sticker-tagger --config config.toml review show <sticker-id>
yogurt-sticker-tagger --config config.toml review approve <sticker-id>
yogurt-sticker-tagger --config config.toml review enable <sticker-id>
yogurt-sticker-tagger --config config.toml export
```

重分类：

```bash
yogurt-sticker-tagger --config config.toml reclassify \
  <sticker-id-1> \
  <sticker-id-2>
```

Web Admin：

```bash
yogurt-sticker-tagger --config config.toml admin
```

Tagger 的配置、令牌、Provider 参数和详细排障以独立仓库 README 为准。

## 稳定接口

两个项目之间唯一必须稳定的数据接口是：

```text
Yogurt_Sticker_Tagger/data/stickers.json
Yogurt_Sticker_Tagger/data/images/
```

权威 Schema：

```text
Yogurt_Sticker_Tagger/schemas/sticker-catalog-v1.schema.json
```

Chat Bot 不维护重复的 `stickers.schema.json`，也不读取 Tagger 的数据库。

## 导出规则

只有满足以下条件的资产进入正式 Catalog：

```text
status = approved
enabled = true
```

无风险资产必须保存为：

```json
{
  "content_warnings": ["none"]
}
```

空数组不再表示“无风险”；`"none"` 也不能与真实风险项共存。

## 同步到 Chat Bot

```bash
cp \
  ~/Yogurt_Sticker_Tagger/data/stickers.json \
  ~/Yogurt_Chat_Bot/assets/stickers/stickers.json

rsync -a \
  ~/Yogurt_Sticker_Tagger/data/images/ \
  ~/Yogurt_Chat_Bot/assets/stickers/images/
```

必须同步 Catalog 和正式图片。缩略图只用于审核页面，不是聊天运行时依赖。

## 同步后验证

```bash
cd ~/Yogurt_Chat_Bot

bash scripts/test-sticker-explicit-fallback
bash scripts/test-sticker-auto-delivery
```

微信端正常日志应包括：

```text
stickerCandidates=...
stickerSelected=sticker-...
stickerDecision=explicit|proactive
sticker outbound scheduled trigger=agent_end
sticker media sent trigger=channel_outbound_adapter
```

## 安全边界

- 原图、标准图、缩略图、SQLite、Catalog 和令牌默认不提交 Git；
- Tagger 的 Web Admin 非本机监听必须设置长随机令牌；
- Chat Bot 只读取正式 Catalog；
- 模型不能指定任意本地路径；
- 只有 `approved + enabled + content_warnings=["none"]` 资产可进入安全发送候选；
- 图片发送失败保留文字回复。
