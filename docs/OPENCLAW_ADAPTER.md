# V1 OpenClaw 前置注入适配器

## 最终结论

Yogurt Social Layer V1 使用 OpenClaw 原生插件的 `before_prompt_build` typed hook。

该 hook 在主模型调用前执行。插件读取当前消息和渠道身份，调用本地 Python bridge 生成 `Yogurt Social State`，并返回：

```ts
{ prependContext: socialContext }
```

社交状态属于每轮动态上下文，因此最终实现使用 `prependContext`，而不是把内容写入用户消息或长期系统提示词。

插件不会修改 `event.prompt`，不会增加第二次大模型调用，也不会从普通聊天中自动写入长期记忆。

## 最终运行环境

V1 已在以下环境完成微信实机验收：

```text
OpenClaw：2026.7.1-2
微信插件：@tencent-weixin/openclaw-weixin 2.4.6
模型：ollama/qwen3.5:9b
渠道：微信官方 Bot 私聊
```

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
  ├── 读取 channel 与 senderId
  ├── 合并插件注册配置和每轮解析配置
  ├── 映射为 Yogurt user_id
  ├── 通过 stdin 调用本地 Python bridge
  ├── SocialLayerService.analyze(user_id, message)
  └── 获取 social_context
  ↓
返回 prependContext
  ↓
OpenClaw 原有 Agent、工具与 Qwen 调用
  ↓
微信回复
```

## Python bridge 输出

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

说明：Python bridge 中的 `final_model_input` 是用于测试的逻辑计划，其中旧字段名 `prepend_system_context` 仅表示“附加上下文”，不代表 OpenClaw 最终使用的 hook 返回字段。OpenClaw 插件最终返回的是：

```ts
{ prependContext: socialContext }
```

实际 hook 只注入 `social_context`，不会把它和用户消息重新拼接。

## 为什么选择 `before_prompt_build`

- **skill**：只有模型决定调用时才运行，不能保证每条消息都在主模型前执行。
- **内部 HOOK.md**：适合命令或 Gateway 生命周期事件，不适合每轮动态 prompt 注入。
- **message_received**：发生阶段过早，也不是最终 prompt 修改接口。
- **修改 OpenClaw 核心**：升级成本高、回滚困难。
- **第二个 Planner 模型**：增加延迟、资源占用和不可解释性，不符合 V1 范围。

`before_prompt_build` 可以在主模型调用前添加动态上下文，同时保持用户消息不变。

## 插件形态

插件入口使用：

```ts
import { definePluginEntry } from "openclaw/plugin-sdk/plugin-entry";
```

这是 hook-only 插件，不是 `defineToolPlugin` 工具插件。

因此不应使用：

```bash
openclaw plugins validate --entry ./index.ts
```

该命令会要求 `defineToolPlugin` 元数据，并拒绝 hook-only 入口。

## 安装

在仓库根目录执行：

```bash
cd ~/Yogurt_Chat_Bot
git switch main
git pull --ff-only origin main
bash scripts/install-openclaw-adapter
```

跳过自动重启：

```bash
bash scripts/install-openclaw-adapter --no-restart
```

安装脚本会：

1. 运行 Python 单元测试；
2. 运行 bridge JSON 模拟；
3. 运行 TypeScript hook → Python → SQLite 跨层测试；
4. 检查插件包结构；
5. 备份 OpenClaw 配置；
6. 使用 `openclaw plugins install --link` 安装本地插件；
7. 写入插件权限和运行配置；
8. 创建或更新 `owner` 用户；
9. 验证配置；
10. 重启 Gateway 并执行 runtime inspect。

当前 OpenClaw 版本不允许组合：

```text
--link + --force
```

正确命令是：

```bash
openclaw plugins install --link "$HOME/Yogurt_Chat_Bot/integrations/openclaw"
```

## 默认数据库

OpenClaw 插件和仓库 CLI 统一使用：

```text
~/.openclaw/yogurt-social/memory.sqlite3
```

覆盖路径：

```bash
YOGURT_SOCIAL_DB="$HOME/.local/share/yogurt-chat-bot/memory.sqlite3" \
  bash scripts/install-openclaw-adapter
```

## 必需权限

```json
{
  "plugins": {
    "entries": {
      "yogurt-social-injector": {
        "hooks": {
          "allowConversationAccess": true,
          "allowPromptInjection": true,
          "timeouts": {
            "before_prompt_build": 12000
          }
        }
      }
    }
  }
}
```

- `allowConversationAccess`：允许插件读取当前对话输入。
- `allowPromptInjection`：允许 hook 返回动态上下文。
- `before_prompt_build` timeout：OpenClaw hook 总预算。

Python bridge 自身默认超时为 `2500 ms`。

## 用户映射

默认单用户私聊配置：

```text
未知 senderId → owner
owner → Domanic / relationship=owner
```

映射优先级：

1. `senderMap[senderId]`
2. `ownerSenderIds` 中的 senderId → `defaultUserId`
3. `unknownSenderPolicy`

可选策略：

- `default`：未知发送者使用 `defaultUserId`
- `pseudonymous`：根据 channel + senderId 生成稳定哈希用户 ID
- `skip`：未知发送者跳过社交层

明确映射示例：

```bash
openclaw config set \
  plugins.entries.yogurt-social-injector.config.senderMap \
  '{"CHANNEL_SCOPED_SENDER_ID":"owner"}' \
  --strict-json

openclaw gateway restart
```

不得把真实微信 Token、登录凭据或完整 OpenClaw 配置提交到仓库。

## 路径与配置兼容处理

最终插件会：

- 读取注册阶段的 `api.pluginConfig`
- 读取每轮 `event.context.pluginConfig`
- 合并两者，以每轮解析配置优先
- 对 `api.resolvePath()` 返回 `undefined` 进行回退
- 对 `api.resolvePath()` 抛出异常进行回退
- 手动展开 `~/`
- 检查 Python source 目录是否存在

该兼容处理修复了实机测试中的：

```text
The "path" argument must be of type string. Received undefined
```

## 子进程边界

- 使用 `spawn` 和参数数组，不经过 shell
- 当前消息通过 stdin JSON 传递
- Python stderr 会被排空，但不会注入模型或输出消息正文
- stdout 上限为 1 MiB
- 默认 bridge 超时为 2500 ms
- 超时后终止 Python 子进程
- 非零退出码、非法 JSON、路径错误均触发 fail-open

## 本地测试

```bash
cd ~/Yogurt_Chat_Bot
bash scripts/test-openclaw-adapter
```

测试包括：

- memory/router 单元测试
- adapter 单元测试
- 显式共同经历检索
- Python stdin/stdout bridge
- TypeScript hook → Python → SQLite
- SQLite 初始化失败 fail-open
- Python 启动失败 fail-open
- 原始消息逐字保留
- `prependContext` 返回断言

最终预期：

```text
Ran 13 tests
OK
bridge simulation: ok
fail-open simulation: ok
typescript hook e2e: ok
```

## 手动 bridge 测试

```bash
export PYTHONPATH="$PWD/social_layer/src"

printf '%s' \
  '{"user_id":"owner","message":"OpenClaw 配置报错了，怎么修复？"}' \
  | python3 -m yogurt_social.openclaw_adapter \
      --db "$HOME/.openclaw/yogurt-social/memory.sqlite3" \
      --pretty
```

## 运行时验证

```bash
openclaw config validate
openclaw plugins doctor
openclaw plugins inspect yogurt-social-injector --runtime --json
openclaw gateway status --deep --require-rpc
```

成功结果应包含：

```text
status: loaded
shape: hook-only
hookCount: 1
typedHooks: before_prompt_build
allowPromptInjection: true
allowConversationAccess: true
diagnostics: []
```

## 微信端验证

依次发送：

```text
酸奶在吗
```

预期：日常、简短、自然。

```text
OpenClaw 配置报错了，怎么修复？
```

预期：技术场景，步骤清晰。

```text
模型又报错了，我真的很难过
```

预期：情绪优先，先回应难过。

```text
我们之前一起完成的 OpenClaw 适配器，下一步做什么？
```

若已经显式写入相关共同经历，回复应自然利用该记忆。

实时日志应出现：

```text
yogurt-social-injector: injection start ...
yogurt-social-injector: injection ready bridgeMs=... contextChars=...
outbound: text sent OK
```

## fail-open 验证

临时破坏 Python 路径：

```bash
openclaw config set \
  plugins.entries.yogurt-social-injector.config.pythonPath \
  /nonexistent/yogurt-python

openclaw gateway restart
```

发送微信消息。预期：

- 日志记录一次 `fail-open`
- 微信仍由原来的 OpenClaw + Qwen 正常回复
- 插件不记录消息正文

恢复：

```bash
openclaw config set \
  plugins.entries.yogurt-social-injector.config.pythonPath \
  "$(command -v python3)"

openclaw gateway restart
```

## 关闭、卸载与回滚

关闭插件：

```bash
openclaw plugins disable yogurt-social-injector
openclaw gateway restart
```

卸载并保留 SQLite：

```bash
bash scripts/uninstall-openclaw-adapter
```

卸载并删除 SQLite：

```bash
bash scripts/uninstall-openclaw-adapter --purge-data
```

安装脚本会在 OpenClaw 配置旁创建备份：

```text
openclaw.json.pre-yogurt-adapter.<timestamp>.backup
```

## 已知非阻塞日志

### `plugins.allow is empty`

这是非 bundled 插件显式信任提醒，不等于插件加载失败。

### `tools.allow removed ...`

这是 Agent 工具白名单日志，与本插件无关。

### `sendTyping` DNS 错误

只影响“正在输入”状态。正文是否成功以此为准：

```text
outbound: text sent OK
```

### hook-only compatibility info

OpenClaw 会把插件标记为 hook-only compatibility path。V1 实机测试证明该形态可以正常加载和运行。

## V1 边界

V1 只读取显式写入的：

- 用户关系
- facts
- preferences
- episodes

V1 不会：

- 从聊天自动学习或写入长期记忆
- 调用额外大模型分类
- 添加 Planner → Replyer 链
- 发送表情包
- 修改 Ollama context length 或 thinking
- 修改 OpenClaw 核心源码

最终验收与归档记录见：

```text
docs/archive/V1_RELEASE.md
```
