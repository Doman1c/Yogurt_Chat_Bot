# Yogurt Social Injector（OpenClaw 前置注入插件）

这是 Yogurt Social Layer V1 的原生 OpenClaw 插件。

插件注册 `before_prompt_build` hook，在每一轮符合条件的用户消息进入主模型之前，调用本地 Python bridge，并把生成的 `Yogurt Social State` 作为动态 `prependContext` 注入。

## 运行流程

```text
OpenClaw 当前用户消息
  ↓
before_prompt_build
  ↓
读取 senderId / channel / pluginConfig
  ↓
映射 Yogurt user_id
  ↓
stdin JSON 调用 Python bridge
  ↓
SocialLayerService.analyze(user_id, message)
  ↓
返回 social_context
  ↓
prependContext
  ↓
原有 OpenClaw Agent 与 Qwen 流程
```

## 核心保证

- 不修改 `event.prompt`
- 不重新拼接用户消息
- 不增加第二次大模型调用
- 不从普通聊天自动写入长期记忆
- Python、SQLite 或 bridge 故障时 fail-open
- 不把消息正文、Token 或数据库内容写入插件日志

## 运行时兼容处理

插件会：

- 合并注册阶段配置与每轮 `event.context.pluginConfig`
- 对 `api.resolvePath()` 返回空值或抛出异常进行回退
- 展开 `~/` 本地路径
- 使用 `spawn` 参数数组，不经过 shell
- 通过 stdin 传递当前消息
- 限制 bridge 输出大小
- 对 Python 子进程设置超时

## 安装

从仓库根目录运行：

```bash
bash scripts/install-openclaw-adapter
```

手动链接安装：

```bash
openclaw plugins install --link "$HOME/Yogurt_Chat_Bot/integrations/openclaw"
```

当前 OpenClaw 版本中，`--link` 不应与 `--force` 一起使用。

## 验证

```bash
openclaw config validate
openclaw plugins doctor
openclaw plugins inspect yogurt-social-injector --runtime --json
openclaw gateway restart
```

成功的运行时信息应包含：

```text
status: loaded
shape: hook-only
before_prompt_build
allowPromptInjection: true
allowConversationAccess: true
```

实时日志：

```bash
openclaw logs --follow --local-time
```

成功注入时：

```text
yogurt-social-injector: injection start ...
yogurt-social-injector: injection ready bridgeMs=... contextChars=...
```

若社交层故障，会记录：

```text
yogurt-social-injector: social layer unavailable (...); fail-open
```

此时 OpenClaw 应继续调用原模型，不应阻断回复。

## 配置

完整配置示例见：

```text
integrations/openclaw/config.example.json
```

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

## 测试

```bash
bash scripts/test-openclaw-adapter
```

测试覆盖 Python 单元测试、bridge JSON、真实 TypeScript hook → Python → SQLite 调用，以及故障时的 fail-open。

## 关闭与卸载

```bash
openclaw plugins disable yogurt-social-injector
openclaw gateway restart
```

```bash
bash scripts/uninstall-openclaw-adapter
```

详细设计、安装、用户映射和回滚说明见：

```text
docs/OPENCLAW_ADAPTER.md
```
