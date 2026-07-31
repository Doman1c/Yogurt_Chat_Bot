# Yogurt Social Injector（OpenClaw 混合插件）

这是 Yogurt Chat Bot 的原生 OpenClaw 插件。目前同时提供：

- V1 `before_prompt_build` 社交上下文注入；
- V2 表情包候选注入；
- V2 `choose_sticker(sticker_id)` 受限选择工具。

V2 当前只完成“候选与选择”阶段。工具会记录 pending decision，但不会立即发送图片，也不会消耗冷却。

## 运行流程

```text
OpenClaw 当前用户消息
  ↓
before_prompt_build
  ↓
Python bridge
  ├── V1 SocialLayerService
  └── V2 StickerRuntime 候选召回
  ↓
prependContext
  ├── Yogurt Social State
  └── Yogurt Sticker Candidates（可能为空）
  ↓
Qwen 生成回复
  ├── 可以不选择表情包
  └── 可以调用 choose_sticker(sticker_id)
  ↓
插件验证该 ID 是否属于本轮候选快照
  ↓
记录 pending decision
```

## `choose_sticker` 安全边界

模型只能传入本轮候选列表中的 `sticker_id`。插件会拒绝：

- 任意本地路径；
- 不属于本轮候选的 ID；
- 已过期的候选；
- 旧轮次的候选；
- 同一轮第二个不同的表情包。

同一个 ID 的重复调用按幂等成功处理。候选快照默认约五分钟过期，并限制内存中的快照数量。

## 核心保证

- 不修改 `event.prompt`；
- 不重新拼接用户原始消息；
- 不增加第二次聊天大模型调用；
- 不在聊天时调用视觉模型；
- 不从普通聊天自动写入长期记忆；
- Python、SQLite、Catalog 或 bridge 故障时 fail-open；
- V2 关闭时维持 V1 原行为；
- 不把消息正文、Token、绝对图片路径或数据库内容写入日志。

## 安装

```bash
bash scripts/install-openclaw-adapter
```

安装脚本默认写入：

```json
{
  "stickerEnabled": false,
  "stickerConfigPath": "<repo>/social_layer/config/stickers.json"
}
```

所以升级插件不会自动开始选图。准备好 `stickers.json` 后再显式开启。

## 验证

```bash
openclaw config validate
openclaw plugins doctor
openclaw plugins inspect yogurt-social-injector --runtime --json
openclaw gateway restart
```

运行时信息应同时包含：

```text
before_prompt_build
choose_sticker
```

实时日志：

```bash
openclaw logs --follow --local-time
```

正常注入示例：

```text
yogurt-social-injector: injection start ...
yogurt-social-injector: injection ready bridgeMs=... contextChars=... stickerCandidates=...
```

模型成功选择时只记录：

```text
yogurt-social-injector: sticker selection pending
```

不会记录具体 sticker ID 或本地路径。

## 配置

主要字段：

- `enabled`
- `pythonPath`
- `repoPath`
- `dbPath`
- `defaultUserId`
- `senderMap`
- `ownerSenderIds`
- `unknownSenderPolicy`
- `allowedChannels`
- `onlyUserTurns`
- `timeoutMs`
- `maxMemories`
- `stickerEnabled`
- `stickerConfigPath`

完整示例见 `integrations/openclaw/config.example.json`。

V2 运行参数与图片 Catalog 分离：

```text
social_layer/config/stickers.json      # 候选数量、阈值、未来冷却参数
assets/stickers/stickers.json          # Tagger 导出的图片与标签
```

## 测试

```bash
bash scripts/test-openclaw-adapter
```

测试覆盖：

- V1 Python 社交层与 fail-open；
- Tagger Catalog → bridge 候选输出；
- TypeScript hook → Python → SQLite；
- `choose_sticker` 注册；
- 非候选 ID 拒绝；
- 同轮最多一张；
- 旧候选替换与过期清理。

## 关闭与卸载

只关闭 V2、保留 V1：

```text
plugins.entries.yogurt-social-injector.config.stickerEnabled = false
```

关闭整个插件：

```bash
openclaw plugins disable yogurt-social-injector
openclaw gateway restart
```

完整卸载：

```bash
bash scripts/uninstall-openclaw-adapter
```
