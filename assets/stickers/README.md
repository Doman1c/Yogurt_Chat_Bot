# 酸奶表情包资产目录

本目录是 Yogurt Chat Bot V2 的本地表情资产入口。

自动分类、审核和导出由独立仓库 `Doman1c/Yogurt_Sticker_Tagger` 完成。Chat Bot 只读正式 Catalog，不访问 Tagger 的 SQLite，也不会在聊天时调用视觉模型。

## 当前状态

V2 已于 2026-08-02 完成微信实机验收。

当前本地验收基线：

```text
14 张 approved + enabled 表情包
Catalog schema_version = 1.0
content_warnings = ["none"]
```

真实图片和正式 `stickers.json` 是本地运行数据，不提交 Git。

## 唯一 Catalog 契约

权威 Schema 位于 Tagger：

```text
Yogurt_Sticker_Tagger/schemas/sticker-catalog-v1.schema.json
```

Chat Bot 直接消费 Tagger 导出的字段，包括：

```text
id
file
status
enabled
description
scenes
emotions
tones
keywords
relationships
content_warnings
weight
cooldown_seconds
confidence
classification
image
```

不要在 Chat Bot 中引入第二套 Schema，也不要使用旧字段名，例如：

```text
tone
content_warning
reviewed
```

## 本地目录

```text
assets/stickers/
├── inbox/                  # 可选，本仓库不负责正式分类
├── images/                 # Tagger 导出的标准化图片
├── thumbnails/             # 可选审核缩略图，聊天运行时不需要
├── stickers.json           # 正式 Catalog
├── stickers.example.json   # 脱敏示例
└── README.md
```

`.gitignore` 排除真实图片、缩略图、Catalog 和缓存。

## 在 Tagger 中处理资产

```bash
cd ~/Yogurt_Sticker_Tagger
source .venv/bin/activate

yogurt-sticker-tagger --config config.toml doctor
yogurt-sticker-tagger --config config.toml scan
yogurt-sticker-tagger --config config.toml review list --status pending
yogurt-sticker-tagger --config config.toml review approve <sticker-id>
yogurt-sticker-tagger --config config.toml review enable <sticker-id>
yogurt-sticker-tagger --config config.toml export
```

只有以下资产进入正式导出：

```text
status = approved
enabled = true
```

没有风险时必须使用：

```json
["none"]
```

不能使用空数组，也不能把 `"none"` 与真实风险项同时保存。

## 同步到 Chat Bot

```bash
cp \
  ~/Yogurt_Sticker_Tagger/data/stickers.json \
  ~/Yogurt_Chat_Bot/assets/stickers/stickers.json

rsync -a \
  ~/Yogurt_Sticker_Tagger/data/images/ \
  ~/Yogurt_Chat_Bot/assets/stickers/images/
```

Catalog 和 `images/` 必须一起同步，因为 Catalog 中的 `file` 是相对路径：

```text
images/sticker-0123456789ab.webp
```

缩略图只用于 Tagger 管理界面，无需同步到聊天运行时。

## 同步后验证

```bash
cd ~/Yogurt_Chat_Bot

python3 - <<'PY'
import json
from pathlib import Path

root = Path("assets/stickers")
catalog = json.loads((root / "stickers.json").read_text(encoding="utf-8"))
stickers = catalog.get("stickers", [])

print("schema_version:", catalog.get("schema_version"))
print("sticker_count:", len(stickers))

problems = []
for sticker in stickers:
    sticker_id = sticker.get("id")
    path = root / sticker.get("file", "")
    checks = {
        "approved": sticker.get("status") == "approved",
        "enabled": sticker.get("enabled") is True,
        "safe": sticker.get("content_warnings") == ["none"],
        "file": path.is_file(),
    }
    print(sticker_id, checks)
    if not all(checks.values()):
        problems.append((sticker_id, checks))

if catalog.get("schema_version") != "1.0":
    raise SystemExit("Catalog schema_version 必须为 1.0")
if problems:
    raise SystemExit(f"Catalog 验证失败: {problems}")
print("Catalog 验证通过")
PY
```

当前实机基线应显示 14 张；未来新增资产后，数量可以增加，不应在运行时代码中写死 14。

## Sticker Runtime 配置

复制配置模板：

```bash
cp \
  social_layer/config/stickers.example.json \
  social_layer/config/stickers.json
```

主要字段：

```json
{
  "enabled": true,
  "catalog_path": "assets/stickers/stickers.json",
  "minimum_score": 5.0,
  "max_candidates": 5,
  "require_files": true,
  "explicit_min_score": 5.0,
  "proactive_min_score": 7.0,
  "proactive_min_margin": 2.0,
  "delivery_delay_ms": 1500,
  "delivery_max_bytes": 10485760,
  "global_cooldown_seconds": 300,
  "scene_cooldown_seconds": 900,
  "default_sticker_cooldown_seconds": 1800,
  "max_stickers_per_hour": 4
}
```

插件配置还必须开启：

```text
stickerEnabled = true
stickerSelectionMode = auto
allowConversationAccess = true
allowPromptInjection = true
```

## 当前运行时能力

`social_layer/src/yogurt_social/stickers/`：

- 加载和验证 Tagger Catalog；
- 路径逃逸与缺失文件检查；
- `approved + enabled` 过滤；
- `content_warnings=["none"]` 严格过滤；
- 场景、情绪、关键词和关系评分；
- 明确请求安全 fallback；
- Top-K 候选输出。

`integrations/openclaw/`：

- `before_prompt_build` 候选召回；
- auto 单次推理 Top 1 选择；
- tool 调试选择；
- 全局、Scene、单图冷却；
- 每小时发送限制；
- 禁止图片描述 Prompt；
- `agent_end` / `message_sent` 调度；
- `openclaw-weixin` outbound adapter 图片发送；
- 发送成功后提交冷却。

## 发送链路

```text
Catalog 候选
→ 本地选中稳定 Sticker ID
→ realpath 限制在 assets/stickers/
→ 普通文件和大小检查
→ loadAdapter("openclaw-weixin")
→ adapter.sendMedia
→ 微信 CDN
→ 微信图片消息
```

WebP 已在微信实机链路中成功发送，不需要为了兼容性批量转成 PNG。

## 安全约束

运行时只接受：

- `schema_version = "1.0"`；
- Catalog 内稳定 ID；
- `images/` 下合法相对路径；
- 实际存在的普通文件；
- 已批准、已启用、无风险的资产；
- 不超过配置大小上限的文件。

模型永远不能指定任意本地绝对路径，也不会看到候选图片描述或文件路径。

## 故障排查

### 候选为 0

检查：

```text
status
enabled
content_warnings
relationships
file path
Sticker Runtime config
```

### 已选中但没有图片

确认 OpenClaw runtime inspect 包含：

```text
agent_end
```

如果被 blocked，设置：

```bash
openclaw config set \
  'plugins.entries.yogurt-social-injector.hooks.allowConversationAccess' \
  'true' \
  --strict-json
```

### 发送失败

查找：

```text
sticker media send failed trigger=channel_outbound_adapter (...)
```

失败时正文仍会保留。
