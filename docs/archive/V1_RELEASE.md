# Yogurt Social Layer V1 最终归档

## 归档状态

```text
版本：V1.0
状态：ACCEPTED / PASS
验收日期：2026-07-31
开发分支：feature/social-layer-v1
归档分支：archive/social-layer-v1-2026-07-31
目标分支：main
```

V1 已完成设计、实现、本地测试、OpenClaw 插件安装和微信官方 Bot 实机验收。本文件用于冻结 V1 的最终范围、运行基线、验收证据和回滚方式。

## V1 目标

V1 的目标是在不修改 OpenClaw 核心、不增加第二次大模型调用、不覆盖用户原始消息的前提下，为每一轮微信私聊消息生成并注入可解释的社交上下文。

输入：

```text
稳定 user_id + 当前用户消息
```

核心调用：

```python
SocialLayerService.analyze(user_id, message)
```

输出：

```text
Yogurt Social State
```

最终通过 OpenClaw `before_prompt_build` 以动态 `prependContext` 注入主模型。

## 最终实现范围

### 社交层

- 规则式场景识别
- 日常、技术、玩笑、安慰、庆祝、严肃六类回复策略
- 情绪场景优先级
- SQLite 用户关系存储
- 明确事实存储
- 明确偏好和不喜欢项存储
- 共同经历存储与相关性检索
- 可解释的社交状态文本生成
- 本地 CLI
- Python 单元测试

### OpenClaw 适配器

- 原生 `definePluginEntry` hook-only 插件
- `before_prompt_build`
- senderId → Yogurt user_id 映射
- 当前消息通过 stdin JSON 传递
- Python bridge 子进程超时
- stdout 大小限制
- `~/` 路径展开
- `api.resolvePath()` 空值和异常回退
- 每轮解析后配置读取
- 原始消息保持不变
- 动态 `prependContext` 注入
- fail-open
- 安装、测试和卸载脚本

## 最终数据流

```text
微信官方 Bot 私聊
  ↓
@tencent-weixin/openclaw-weixin
  ↓
OpenClaw Agent 构建本轮 prompt
  ↓
before_prompt_build
  ↓
Yogurt Social Injector
  ├── 读取 channel 与 senderId
  ├── 映射 user_id
  ├── 调用 Python bridge
  ├── 读取 SQLite 显式记忆
  └── 生成社交上下文
  ↓
prependContext
  ↓
Ollama / Qwen
  ↓
微信正文发送
```

## 验收环境

```text
操作系统：macOS
OpenClaw：2026.7.1-2
微信插件：@tencent-weixin/openclaw-weixin 2.4.6
模型提供方：Ollama
模型：qwen3.5:9b
Thinking：off
渠道：微信官方 Bot 私聊
```

## 自动测试结果

V1 最终测试范围：

- SQLite 用户与记忆读写
- fact upsert
- 相关偏好检索
- 默认日常场景
- 技术场景
- 庆祝场景
- 情绪优先于技术关键词
- 适配器不自动写入记忆
- 适配器禁用时不调用社交层
- 原始消息逐字保留
- Python module bridge JSON 输出
- SQLite 初始化失败时 fail-open
- TypeScript hook → Python bridge → SQLite 端到端调用
- Python 启动失败时 fail-open

最终测试结果：

```text
Ran 13 tests
OK
bridge simulation: ok
fail-open simulation: ok
typescript hook e2e: ok
```

## 实机验收结果

### 插件运行状态

OpenClaw runtime inspect 已确认：

```text
plugin id: yogurt-social-injector
status: loaded
shape: hook-only
hookCount: 1
typed hook: before_prompt_build
allowPromptInjection: true
allowConversationAccess: true
diagnostics: []
```

### 微信链路

连续多轮消息均出现：

```text
inbound message
injection start
injection ready
outbound: text sent OK
```

最终四轮实测数据：

| 轮次 | bridge 耗时 | 注入字符数 | 结果 |
|---|---:|---:|---|
| 1 | 87 ms | 275 | 文本发送成功 |
| 2 | 67 ms | 296 | 文本发送成功 |
| 3 | 64 ms | 294 | 文本发送成功 |
| 4 | 50 ms | 296 | 文本发送成功 |

这证明：

1. 每条符合条件的微信消息都会调用 injector；
2. Python bridge 能稳定生成社交上下文；
3. 注入后 Qwen 能正常生成回复；
4. 微信正文可以正常发送；
5. V1 没有增加额外大模型调用。

## fail-open 验收

早期实机测试中曾出现路径解析异常：

```text
social layer unavailable (The "path" argument must be of type string. Received undefined); fail-open
```

随后仍然出现：

```text
outbound: text sent OK
```

这证明社交层故障时，原来的 OpenClaw + Qwen + 微信回复链路仍然能够继续。

路径问题已通过以下方式修复：

- `api.resolvePath()` 返回非字符串时回退；
- `api.resolvePath()` 抛出异常时回退；
- 手动展开 `~/`；
- 每轮合并 registration config 与 `event.context.pluginConfig`。

## 已知非阻塞日志

### `plugins.allow is empty`

这是非 bundled 插件的显式信任提醒，不代表 V1 注入失败。后续可以根据 OpenClaw 安全策略配置 allowlist。

### `tools.allow removed ...`

这是 Agent 工具白名单生效的正常日志，与 Yogurt Social Injector 无关。

### `sendTyping` DNS 失败

该错误只影响微信“正在输入”状态。只要出现：

```text
outbound: text sent OK
```

正文即已成功发送。

### hook-only compatibility info

OpenClaw 将当前插件识别为 hook-only compatibility path。该形态在验收版本中可正常加载和运行，不属于阻塞错误。

## V1 冻结边界

V1 不包含：

- 普通聊天自动写入长期记忆
- 自动提取记忆并直接落库
- Planner → Replyer 双模型结构
- 第二次大模型分类调用
- 表情包自动选择与发送
- 表达库自动学习
- 群聊
- 多微信账号共享官方 Bot 实例

任何后续开发都应进入新版本，不直接改变本归档中的 V1 定义。

## 核心不变量

后续版本不得破坏：

1. **当前用户消息保持原样。**
2. **社交层故障不能阻断主回复。**
3. **V1 不从普通聊天自动写入长期记忆。**
4. **V1 不增加第二次大模型调用。**
5. **不得在日志或仓库中暴露 Token、消息正文或未脱敏数据。**

## 安装与验证

```bash
cd ~/Yogurt_Chat_Bot
git switch main
git pull --ff-only origin main
bash scripts/install-openclaw-adapter
```

```bash
openclaw config validate
openclaw plugins doctor
openclaw plugins inspect yogurt-social-injector --runtime --json
openclaw gateway status --deep --require-rpc
```

## 回滚

只停用社交层：

```bash
openclaw plugins disable yogurt-social-injector
openclaw gateway restart
```

卸载并保留数据库：

```bash
bash scripts/uninstall-openclaw-adapter
```

卸载并删除数据库：

```bash
bash scripts/uninstall-openclaw-adapter --purge-data
```

## 归档结论

Yogurt Social Layer V1 与 OpenClaw 前置注入适配器已经达到既定要求，可以作为后续 V1.1 或 V2 的稳定基线。
