# Yogurt Chat Bot（酸奶）

酸奶是一个运行在 **OpenClaw + Ollama + 微信官方 Bot** 之上的本地 AI 角色项目。

项目目标不是单纯给本地模型套一层聊天界面，而是在不增加第二次大模型调用的前提下，为每一轮消息补充稳定、可解释、可回滚的社交上下文，让酸奶能够根据当前场景、用户关系、明确写入的偏好和共同经历调整回复方式。

## 当前状态

**V1 已于 2026-07-31 完成验收并进入稳定归档。**

V1 已验证的完整链路：

```text
微信私聊消息
  ↓
@tencent-weixin/openclaw-weixin
  ↓
OpenClaw before_prompt_build
  ↓
Yogurt Social Injector
  ↓
Python SocialLayerService
  ├── 场景识别
  ├── 回复风格路由
  ├── 用户关系读取
  ├── 偏好与共同经历检索
  └── 生成 Yogurt Social State
  ↓
prependContext 动态注入
  ↓
Ollama / Qwen
  ↓
微信回复
```

最终实机验收环境：

- macOS
- OpenClaw `2026.7.1-2`
- `@tencent-weixin/openclaw-weixin` `2.4.6`
- Ollama
- `qwen3.5:9b`
- 微信官方 Bot 私聊

连续多轮实机日志均完成：

```text
inbound message
injection start
injection ready
outbound: text sent OK
```

社交层实测耗时约为 `50–87 ms`，单轮注入上下文约为 `275–296` 个字符。

## V1 已实现功能

### 1. 规则式场景识别

V1 不额外调用分类模型，使用本地规则识别当前消息场景：

- `casual`：日常交流
- `technical`：技术排障与配置问题
- `playful`：玩笑与轻松互动
- `comfort`：安慰和情绪支持
- `celebration`：庆祝与积极反馈
- `serious`：严肃问题

情绪类场景具有较高优先级，避免用户表达难过时只得到机械的技术回答。

### 2. 回复风格路由

根据场景生成可解释的回复策略，包括：

- 语气
- 回复长度
- 技术细节程度
- 是否优先安慰
- 是否使用轻松表达
- 是否避免机械化建议

### 3. 用户关系与显式记忆

使用 SQLite 保存：

- 用户基本关系
- 明确事实
- 明确偏好与不喜欢的表达
- 共同经历

V1 只读取明确写入的记忆，不会从普通聊天中自动学习或自动写入长期记忆。

### 4. OpenClaw 前置注入适配器

原生 OpenClaw 插件注册：

```text
before_prompt_build
```

插件在主模型调用前运行，通过本地 Python bridge 调用：

```python
SocialLayerService.analyze(user_id, message)
```

随后将生成的社交状态作为动态 `prependContext` 注入。

插件不会：

- 修改或覆盖用户原始消息
- 把上下文伪装成用户消息
- 增加第二次大模型调用
- 自动写入聊天记忆
- 修改 OpenClaw 核心源码

### 5. 用户映射

支持将渠道中的稳定 `senderId` 映射为 Yogurt 用户 ID：

- 明确 `senderMap`
- `ownerSenderIds`
- 单用户默认 owner
- 稳定匿名 ID
- 未知用户跳过社交层

当前单用户微信私聊默认使用：

```text
owner → Domanic
```

### 6. fail-open 故障保护

以下故障都不会阻断原来的 OpenClaw + Qwen 回复链路：

- Python 无法启动
- SQLite 无法打开
- bridge 超时
- bridge 输出不是合法 JSON
- 输出过大
- 路径配置异常
- SocialLayerService 内部异常

发生故障时，插件返回“无修改”，OpenClaw 继续执行原模型流程。

## V1 不包含的功能

下列内容属于后续版本规划，不应视为 V1 已完成能力：

- 从普通对话中自动提取并写入长期记忆
- Planner → Replyer 双模型结构
- 额外大模型分类器
- 自动表情包选择与发送
- 表达库自动学习
- 后台主动学习
- 群聊支持
- 多微信账号共享同一官方 Bot 实例

## 项目目录

```text
Yogurt_Chat_Bot/
├── README.md
├── workspace/                       # OpenClaw 人格、行为规则与运行说明
├── social_layer/
│   ├── config/                      # 社交层配置
│   ├── src/yogurt_social/
│   │   ├── memory.py                # SQLite 用户与记忆存储
│   │   ├── router.py                # 场景识别与风格路由
│   │   ├── service.py               # SocialLayerService 统一入口
│   │   ├── openclaw_adapter.py      # stdin/stdout Python bridge
│   │   └── cli.py                   # 本地管理命令
│   └── tests/                       # 单元测试与适配器测试
├── integrations/openclaw/
│   ├── index.ts                     # before_prompt_build 插件入口
│   ├── openclaw.plugin.json         # 插件 manifest 与配置 schema
│   ├── package.json                 # OpenClaw 插件包定义
│   └── config.example.json          # 配置示例
├── scripts/
│   ├── yogurt-social                # 社交层 CLI 启动脚本
│   ├── install-openclaw-adapter     # 安装、配置与验证
│   ├── test-openclaw-adapter        # 完整测试
│   └── uninstall-openclaw-adapter   # 卸载与数据清理
└── docs/
    ├── V1_DESIGN.md                 # V1 设计说明
    ├── OPENCLAW_ADAPTER.md          # OpenClaw 适配器详细文档
    └── archive/V1_RELEASE.md        # V1 最终归档与验收记录
```

## 快速安装

### 前置条件

需要本机已有：

- OpenClaw
- Python 3.9 或更高版本
- Node.js
- Ollama
- 可正常工作的 OpenClaw 模型配置
- 已连接的微信官方 Bot 插件

### 安装 V1 适配器

```bash
cd ~/Yogurt_Chat_Bot
git switch main
git pull --ff-only origin main
bash scripts/install-openclaw-adapter
```

安装脚本会：

1. 运行 Python 单元测试；
2. 运行 Python bridge 模拟；
3. 运行 TypeScript hook → Python → SQLite 端到端测试；
4. 检查插件包结构；
5. 备份当前 OpenClaw 配置；
6. 使用 `--link` 安装本地插件；
7. 写入插件权限与运行路径；
8. 创建或更新本地 `owner` 用户；
9. 验证配置并重启 Gateway。

跳过自动重启：

```bash
bash scripts/install-openclaw-adapter --no-restart
```

## 运行时验证

```bash
openclaw config validate
openclaw plugins doctor
openclaw plugins inspect yogurt-social-injector --runtime --json
openclaw gateway status --deep --require-rpc
```

成功的 runtime inspect 应包含：

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

每轮正常注入应出现：

```text
yogurt-social-injector: injection start ...
yogurt-social-injector: injection ready bridgeMs=... contextChars=...
outbound: text sent OK
```

## 社交层命令行工具

默认数据库：

```text
~/.openclaw/yogurt-social/memory.sqlite3
```

覆盖数据库路径：

```bash
export YOGURT_SOCIAL_DB="$HOME/.local/share/yogurt-chat-bot/memory.sqlite3"
```

创建或更新用户：

```bash
bash scripts/yogurt-social upsert-user \
  --user-id owner \
  --display-name Domanic \
  --relationship owner
```

写入共同经历：

```bash
bash scripts/yogurt-social remember-episode \
  --user-id owner \
  --summary "我们一起完成了 Yogurt Social Layer V1" \
  --importance 0.9
```

分析消息并查看注入上下文：

```bash
bash scripts/yogurt-social analyze \
  --user-id owner \
  --message "OpenClaw 配置报错了，怎么排查？" \
  --context-only
```

## 测试

完整测试：

```bash
cd ~/Yogurt_Chat_Bot
bash scripts/test-openclaw-adapter
```

测试范围包括：

- SQLite 用户与记忆读写
- 事实更新
- 相关偏好检索
- 场景识别
- 情绪优先级
- 原始消息逐字保留
- 禁用适配器时不调用社交层
- Python bridge JSON 输出
- TypeScript hook → Python → SQLite 跨层调用
- Python 启动失败时 fail-open
- SQLite 初始化失败时 fail-open

## 配置说明

插件 ID：

```text
yogurt-social-injector
```

主要配置项：

| 配置项 | 作用 | 默认值 |
|---|---|---|
| `enabled` | 是否执行社交层 | `true` |
| `pythonPath` | Python 可执行文件路径 | `python3` |
| `repoPath` | 本仓库绝对路径 | 自动推断 |
| `dbPath` | SQLite 数据库路径 | `~/.openclaw/yogurt-social/memory.sqlite3` |
| `defaultUserId` | 默认用户 ID | `owner` |
| `senderMap` | senderId 到用户 ID 的显式映射 | `{}` |
| `ownerSenderIds` | 直接映射为 owner 的 senderId | `[]` |
| `unknownSenderPolicy` | 未知用户处理方式 | `default` |
| `allowedChannels` | 允许执行的渠道 | `[]`，即不限制 |
| `onlyUserTurns` | 跳过无用户身份的后台任务 | `true` |
| `timeoutMs` | Python bridge 超时 | `2500` |
| `maxMemories` | 单轮最多检索记忆数量 | `3` |

V1 使用的 OpenClaw 权限：

```json
{
  "allowConversationAccess": true,
  "allowPromptInjection": true,
  "timeouts": {
    "before_prompt_build": 12000
  }
}
```

## 关闭与卸载

只关闭插件：

```bash
openclaw plugins disable yogurt-social-injector
openclaw gateway restart
```

卸载插件并保留 SQLite：

```bash
bash scripts/uninstall-openclaw-adapter
```

卸载并删除本地社交层数据：

```bash
bash scripts/uninstall-openclaw-adapter --purge-data
```

## 故障排查

### 微信没有回复

先关闭社交层确认原链路：

```bash
openclaw plugins disable yogurt-social-injector
openclaw gateway restart
```

如果微信恢复回复，检查：

```bash
openclaw plugins inspect yogurt-social-injector --runtime --json
openclaw logs --follow --local-time
```

### 插件存在但社交层未生效

日志应至少包含：

```text
injection start
```

若随后出现 `fail-open`，检查：

- `repoPath`
- `pythonPath`
- `dbPath`
- SQLite 目录权限
- Python bridge 是否能单独运行

### `sendTyping` 失败

微信“正在输入”状态的 DNS 或网络错误不会必然影响正文；以是否出现下面日志作为正文发送结果：

```text
outbound: text sent OK
```

## 安全说明

禁止提交到仓库：

- API Key、Token、密码
- 微信登录凭据
- OpenClaw 完整主配置
- SQLite 数据库
- 会话记录
- 本地日志
- 未脱敏的用户数据
- 代理认证信息

插件日志只记录必要的运行状态、耗时和字符数量，不应输出消息正文、Token 或数据库内容。

## 文档

- `docs/V1_DESIGN.md`：V1 社交层设计与边界
- `docs/OPENCLAW_ADAPTER.md`：安装、配置、映射、测试和回滚
- `docs/archive/V1_RELEASE.md`：V1 最终归档、实机验收和已知非阻塞问题
- `integrations/openclaw/README.md`：OpenClaw 插件说明

## 版本规划

### V1.0（已完成）

- 社交场景识别
- 回复风格路由
- 显式关系与记忆存储
- OpenClaw 前置注入
- senderId 用户映射
- fail-open
- 测试、安装和回滚

### 后续版本候选

- 更完善的可观测性与运行统计
- 记忆审核和管理界面
- 可控的记忆候选提取流程
- 表情包与表达库
- 多用户隔离策略
- 记忆迁移、备份与导出

后续版本不得破坏 V1 的三个核心保证：

1. 用户当前消息保持原样；
2. 社交层故障不能阻断正常回复；
3. 未经明确设计和审核，不自动写入长期记忆。
