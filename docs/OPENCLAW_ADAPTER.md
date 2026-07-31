# V1 OpenClaw 前置注入适配器

## 结论

V1 使用 OpenClaw 原生插件的 `before_prompt_build` typed hook。

该 hook 在主模型调用前执行，并允许返回：

```ts
{ prependSystemContext: contextBlock }
```

适配器不会修改 `event.prompt`，因此用户最新消息仍由 OpenClaw 按原样保存、
传递和交给 Qwen。社交层只作为本轮附加系统上下文出现，主模型仍只生成一次。

没有使用 skill、内部 `HOOK.md`、额外 Planner 模型或 OpenClaw 核心源码补丁。

## 文件结构

```text
social_layer/src/yogurt_social/openclaw_adapter.py
social_layer/tests/test_openclaw_adapter.py
integrations/openclaw/index.ts
integrations/openclaw/openclaw.plugin.json
integrations/openclaw/package.json
integrations/openclaw/config.example.json
scripts/install-openclaw-adapter
scripts/test-openclaw-adapter
scripts/uninstall-openclaw-adapter
docs/OPENCLAW_ADAPTER.md
```

## 每轮数据流

```text
微信官方 Bot / iLink 私聊消息
  ↓
OpenClaw 构建本轮 prompt
  ↓
before_prompt_build
  ↓
Yogurt Social Injector
  ├── 从 hook context 读取稳定 senderId
  ├── 映射为 Yogurt user_id
  ├── 通过 stdin 调用本地 Python bridge
  ├── SocialLayerService.analyze(user_id, message)
  └── 返回 prependSystemContext
  ↓
OpenClaw 原有 Agent、工具与 Qwen 调用
  ↓
微信回复
```

Python bridge 的输出结构：

```json
{
  "original_message": "用户原始消息",
  "social_context": "[Yogurt Social State]...",
  "final_model_input": {
    "prepend_system_context": "[Yogurt Social State]...",
    "user_message": "用户原始消息"
  },
  "metadata": {
    "enabled": true,
    "applied": true,
    "fail_open": false,
    "scene": "technical",
    "mood": "curious",
    "memory_count": 1,
    "original_message_preserved": true
  }
}
```

`final_model_input` 是可测试的注入计划。实际 OpenClaw hook 只返回
`prependSystemContext`，不把两段文本重新拼接为新的用户消息。

## 为什么不是其他入口

- **skill**：只有模型决定调用时才运行，不保证每条消息都在模型调用前执行。
- **内部 HOOK.md**：适合命令和 Gateway 生命周期事件，不是每轮动态 prompt 注入面。
- **message_received**：主要是观察入站消息，发生阶段过早，也不是官方 prompt 修改接口。
- **before_agent_run**：适合在最终模型输入前检查或阻断，不负责添加 prompt context。
- **修改 OpenClaw 核心**：升级成本高，回滚困难，当前没有必要。

`before_prompt_build` 是官方为动态上下文和系统提示词贡献提供的稳定入口。

## 安装

在目标分支和仓库根目录执行：

```bash
cd ~/Yogurt_Chat_Bot
git switch feature/social-layer-v1
git pull --ff-only origin feature/social-layer-v1
bash scripts/install-openclaw-adapter
```

安装脚本会：

1. 运行 Python 单元测试和本地端到端模拟；
2. 校验 OpenClaw 插件入口；
3. 在本地备份当前 OpenClaw 配置；
4. 通过 `openclaw plugins install --link` 链接插件；
5. 仅修改该插件自己的配置项；
6. 启用 conversation access 与 prompt injection 权限；
7. 创建或更新本地 `owner` 用户；
8. 验证配置并重启 Gateway。

跳过自动重启：

```bash
bash scripts/install-openclaw-adapter --no-restart
```

默认数据库：

```text
~/.openclaw/yogurt-social/memory.sqlite3
```

覆盖路径：

```bash
YOGURT_SOCIAL_DB="$HOME/.local/share/yogurt-chat-bot/memory.sqlite3" \
  bash scripts/install-openclaw-adapter
```

## 必需的 OpenClaw 权限

非内置插件读取当前 prompt 并修改 prompt 构建时，需要：

```json
{
  "plugins": {
    "entries": {
      "yogurt-social-injector": {
        "hooks": {
          "allowConversationAccess": true,
          "allowPromptInjection": true
        }
      }
    }
  }
}
```

安装脚本会写入这些字段。关闭 `allowPromptInjection` 后，OpenClaw 会忽略
prompt 修改结果；这是紧急停用注入的第二道开关。

## 用户映射

V1 当前默认：

```text
未知微信 senderId → owner（当前单用户私聊默认值）
owner → Domanic / relationship=owner
```

该行为由配置控制，不在代码中永久写死。映射优先级：

1. `senderMap[senderId]`
2. `ownerSenderIds` 中的 senderId → `defaultUserId`
3. `unknownSenderPolicy`

可选策略：

- `default`：未知发送者使用 `defaultUserId`，适合当前单用户私聊。
- `pseudonymous`：按 channel + senderId 生成稳定哈希用户 ID。
- `skip`：未知发送者不运行社交层，直接走 OpenClaw 原流程。

配置明确映射：

```bash
openclaw config set \
  plugins.entries.yogurt-social-injector.config.senderMap \
  '{"CHANNEL_SCOPED_SENDER_ID":"owner"}' --strict-json
openclaw gateway restart
```

不得把真实微信 Token、登录凭据或完整 OpenClaw 配置提交到仓库。

## 本地测试

完整测试：

```bash
cd ~/Yogurt_Chat_Bot
bash scripts/test-openclaw-adapter
```

该脚本包括：

- 原有 memory/router 测试；
- 新增 adapter 单元测试；
- 真实 SQLite + 显式共同经历检索；
- Python stdin/stdout bridge 模拟；
- TypeScript hook → Python bridge → SQLite 跨层端到端模拟；
- SQLite 初始化失败和 Python 启动失败时的 fail-open 模拟；
- 原始消息逐字保留断言。

手动 bridge 测试：

```bash
export PYTHONPATH="$PWD/social_layer/src"
printf '%s' \
  '{"user_id":"owner","message":"OpenClaw 配置报错了，怎么修复？"}' \
  | python3 -m yogurt_social.openclaw_adapter --pretty
```

## 运行时验证

```bash
openclaw config validate
openclaw plugins inspect yogurt-social-injector --runtime --json
```

运行时结果应包含已加载插件和 `before_prompt_build` hook。

检查开关：

```bash
openclaw config get \
  plugins.entries.yogurt-social-injector.config.enabled
openclaw config get \
  plugins.entries.yogurt-social-injector.hooks.allowPromptInjection
```

## 微信端验证

依次发送：

```text
酸奶在吗
```

预期为 `casual`，回复简短自然。

```text
OpenClaw 配置报错了，怎么修复？
```

预期为 `technical`，回复精确并给出可执行步骤。

```text
模型又报错了，我真的很难过
```

预期情绪优先，进入 `comfort`，先回应难过而不是只讲技术。

```text
GitHub 仓库的适配器下一步怎么做？
```

若已明确写入相关共同经历，回复应自然利用该记忆，但不得忽略当前问题。

## fail-open 验证

### 关闭适配器

```bash
openclaw config set \
  plugins.entries.yogurt-social-injector.config.enabled false --strict-json
openclaw gateway restart
```

此时微信仍应由原来的 OpenClaw + Qwen 正常回复。

恢复：

```bash
openclaw config set \
  plugins.entries.yogurt-social-injector.config.enabled true --strict-json
openclaw gateway restart
```

### 模拟 Python bridge 故障

先记录当前 Python 路径：

```bash
openclaw config get \
  plugins.entries.yogurt-social-injector.config.pythonPath
```

临时设为不存在的命令：

```bash
openclaw config set \
  plugins.entries.yogurt-social-injector.config.pythonPath \
  /nonexistent/yogurt-python
openclaw gateway restart
```

发送一条微信消息。预期：日志记录一次插件故障，微信仍由原流程回复。
插件不会记录消息正文、Token 或数据库内容。

随后恢复真实路径：

```bash
openclaw config set \
  plugins.entries.yogurt-social-injector.config.pythonPath \
  "$(command -v python3)"
openclaw gateway restart
```

## 超时与故障边界

- Python 子进程默认超时：2500 ms。
- OpenClaw hook budget：12000 ms。
- bridge stdout 上限：1 MiB。
- 使用 `spawn` + 参数数组，不经过 shell。
- 当前消息通过 stdin JSON 传递，不放入命令行参数。
- Python stderr 不注入模型上下文。
- 任意 spawn、超时、退出码、JSON 或 SQLite 错误都会返回无修改结果。

## 关闭、卸载与回滚

只关闭注入：

```bash
openclaw plugins disable yogurt-social-injector
openclaw gateway restart
```

完整卸载，保留 SQLite：

```bash
bash scripts/uninstall-openclaw-adapter
```

卸载并删除本地 SQLite：

```bash
bash scripts/uninstall-openclaw-adapter --purge-data
```

安装脚本会在 OpenClaw 配置旁创建本地备份：

```text
openclaw.json.pre-yogurt-adapter.<timestamp>.backup
```

OpenClaw 自身的 `plugins uninstall` 会移除插件 entry、安装记录、allow/deny
记录和 linked load path，不会修改本仓库代码。

## V1 边界

本适配器只读取显式写入的：

- 用户关系
- facts
- preferences
- episodes

它不会：

- 从聊天自动学习或写入正式记忆；
- 调用额外大模型做分类；
- 添加 Planner → Replyer 链；
- 发送表情包；
- 修改 Ollama context 或 thinking；
- 修改 OpenClaw 核心源码。
