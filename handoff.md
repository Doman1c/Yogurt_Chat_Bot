# Yogurt Chat Bot V2 开发交接

更新时间：2026-08-01 03:54（UTC+8）  
仓库：`Doman1c/Yogurt_Chat_Bot`  
开发分支：`feature/sticker-system-v2`  
稳定基线：`main`（V1 已完成并保持可用）

## 1. 当前结论

V2 表情包系统已经完成以下两层基础能力：

1. **离线资产层**
   - 独立仓库 `Doman1c/Yogurt_Sticker_Tagger`
   - 已完成真实图片分类、审核和 Catalog 导出
   - Catalog 契约固定为 `schema_version: "1.0"`

2. **聊天运行时基础层**
   - Chat Bot 能安全加载 Tagger 导出的 Catalog
   - 能根据 V1 的 `scene / mood / relationship / message` 召回候选
   - 能把少量候选注入当前模型上下文
   - 已注册受限工具 `choose_sticker(sticker_id)`
   - 工具只能选择本轮候选，不能传任意文件路径

当前微信文字回复链路正常，V1 没有被破坏。

当前仍不会发送图片。即使模型调用 `choose_sticker`，系统也只记录短期 pending selection；图片发送与冷却尚未实现。

## 2. 已完成内容

### 2.1 Catalog 契约统一

Chat Bot 不再维护一份与 Tagger 冲突的图片 Schema。

唯一权威契约来自：

```text
Doman1c/Yogurt_Sticker_Tagger
schemas/sticker-catalog-v1.schema.json
```

Chat Bot 当前消费字段包括：

```text
schema_version
generated_at
generated_by
asset_root
stickers[]

sticker.id
sticker.file
sticker.sha256
sticker.status
sticker.enabled
sticker.description
sticker.ocr_text
sticker.scenes
sticker.emotions
sticker.tones
sticker.keywords
sticker.relationships
sticker.avoid_when
sticker.content_warnings
sticker.weight
sticker.cooldown_seconds
sticker.confidence
sticker.classification
```

全局冷却、候选数量和允许渠道等运行设置已经从 Catalog 中拆出，放在：

```text
social_layer/config/stickers.example.json
```

本地正式配置：

```text
social_layer/config/stickers.json
```

该文件已被 `.gitignore` 忽略。

### 2.2 安全 Catalog 加载

新增：

```text
social_layer/src/yogurt_social/stickers/catalog.py
```

已实现：

- 版本检查
- 必填字段检查
- 重复 ID 拦截
- `asset_root` 路径逃逸拦截
- 图片路径 `../` 穿越拦截
- 绝对路径拦截
- 图片缺失检查（可配置）
- 仅接受 Tagger Catalog V1

### 2.3 候选召回

新增：

```text
social_layer/src/yogurt_social/stickers/retriever.py
social_layer/src/yogurt_social/stickers/runtime.py
```

当前评分规则：

```text
scene match         +4
mood match          +3
keyword match       +2 each，最多两个
relationship match  +1
最终分数乘以 sticker.weight
```

强制过滤：

- `status != approved`
- `enabled != true`
- 存在非 `none` 风险标签
- 当前关系不在允许范围
- V1 `use_sticker=false`
- 分数低于阈值

只返回 Top-K 候选。

### 2.4 Python bridge 候选注入

已扩展：

```text
social_layer/src/yogurt_social/openclaw_adapter.py
```

一次 Python bridge 调用现在同时完成：

```text
V1 社交状态分析
+ 显式记忆检索
+ V2 表情候选召回
```

不会增加第二次 LLM 调用。

候选上下文格式：

```text
[Yogurt Sticker Candidates]
- id: sticker-...
  description: ...
  emotions: ...

可以不使用表情包。需要使用时只能调用 choose_sticker，并传入以上 ID。
```

模型上下文中不注入本地绝对路径。

### 2.5 `choose_sticker` 工具

OpenClaw 混合插件已经注册：

```text
choose_sticker(sticker_id)
```

相关文件：

```text
integrations/openclaw/index.ts
integrations/openclaw/sticker-selection.ts
integrations/openclaw/openclaw.plugin.json
```

安全边界：

- 候选与当前 run/session/channel 身份绑定
- 工具只能选择本轮候选 ID
- 不能指定本地路径
- 候选会过期
- 同一轮最多选择一张
- 相同 ID 重复调用为幂等成功
- 同轮尝试更换另一张会被拒绝
- 新一轮候选会替换旧快照
- 当前只保存 pending selection
- 不发送图片
- 不提交冷却

Manifest 已声明：

```json
{
  "contracts": {
    "tools": ["choose_sticker"]
  }
}
```

插件版本已更新为 `0.2.0`。

## 3. 测试状态

本机完整测试已通过：

```text
Ran 22 tests
OK
sticker selection state: ok
bridge simulation: ok
fail-open simulation: ok
typescript hook and tool e2e: ok
```

运行命令：

```bash
cd ~/Yogurt_Chat_Bot
bash scripts/test-openclaw-adapter
```

测试覆盖：

- V1 SQLite 记忆
- V1 场景路由
- 原始消息保持不变
- Python bridge JSON
- V1 fail-open
- Tagger Catalog 契约兼容
- Catalog 路径安全
- pending/disabled/风险图片过滤
- 候选评分与排序
- V1 阻止表情候选
- bridge 候选注入
- 候选上下文不泄露路径
- 当前候选合法选择
- 非候选 ID 拒绝
- 同轮第二张拒绝
- 同 ID 幂等
- 候选过期
- TypeScript hook 与工具注册模拟

## 4. 真实 OpenClaw 验证结果

实机环境：

```text
macOS
OpenClaw 2026.7.1-2
@tencent-weixin/openclaw-weixin 2.4.6
Ollama qwen3.5:9b
微信官方 Bot 私聊
```

确认结果：

```text
plugin status: loaded
plugin version: 0.2.0
before_prompt_build: registered
choose_sticker: registered
OpenClaw plugin doctor: no issues
Gateway: ready
微信 inbound: 正常
社交层 injection: 正常
微信 outbound text sent OK: 正常
```

关键日志：

```text
yogurt-social-injector: registered before_prompt_build and choose_sticker
yogurt-social-injector: injection ready bridgeMs=55 contextChars=259 stickerCandidates=0
outbound: text sent OK
```

`stickerCandidates=0` 当前正常，因为 V2 配置仍关闭。

## 5. 当前已知阻塞点

### 5.1 `choose_sticker` 被 OpenClaw 工具白名单过滤

实机日志明确显示：

```text
tool policy removed ... choose_sticker ... via tools.allow
```

结论：

- 插件和工具注册成功
- 但当前 Agent 的 `tools.allow` 没有包含 `choose_sticker`
- 模型目前看不到该工具

下一次继续开发时，第一步应执行：

```bash
openclaw config get tools.allow
```

先读取当前白名单，再安全追加：

```text
choose_sticker
```

不要直接覆盖原列表。

### 5.2 V2 仍关闭

安装脚本默认写入：

```json
"stickerEnabled": false
```

运行时配置示例也默认：

```json
"enabled": false
```

这是刻意的安全默认值。当前不会加载真实 Catalog，也不会向模型注入候选。

### 5.3 图片发送尚未实现

还没有实现：

```text
reply_payload_sending
本地媒体可信路径
微信图片载荷
发送成功后的冷却提交
发送失败后的 pending rollback
```

因此现在不应开启生产图片发送。

### 5.4 冷却尚未实现

配置字段已经预留：

```text
global_cooldown_seconds
scene_cooldown_seconds
default_sticker_cooldown_seconds
max_stickers_per_hour
```

但 SQLite 冷却状态机尚未完成。

## 6. 下一步建议顺序

### Step 1：修复工具白名单

```bash
openclaw config get tools.allow
```

确认现有内容后，把 `choose_sticker` 追加到原列表，重启 Gateway，并检查：

```bash
openclaw logs --follow --local-time
```

下一轮消息不应再出现：

```text
tool policy removed ... choose_sticker
```

### Step 2：准备真实 Catalog

从 Sticker Tagger 中批准并导出至少 2–5 张表情：

```bash
cd ~/Yogurt_Sticker_Tagger
yogurt-sticker-tagger review list --status pending
yogurt-sticker-tagger review approve <sticker-id>
yogurt-sticker-tagger export
```

将导出的 Catalog 与图片放到 Chat Bot 运行时约定位置，保持 Catalog 中的相对路径可解析。

建议优先测试：

```text
celebration
playful
```

因为 V1 当前允许这两类场景使用表情。

### Step 3：开启候选召回，但仍不发送图片

创建：

```text
social_layer/config/stickers.json
```

以示例文件为基础，把：

```json
"enabled": true
```

然后将插件配置中的：

```json
"stickerEnabled": true
```

重启 Gateway。

预期日志：

```text
stickerCandidates=1
```

或更高，但不应超过 `max_candidates`。

### Step 4：实测 Qwen 工具调用

发送明确庆祝类消息，例如：

```text
酸奶，我终于把这个问题解决了！
```

观察：

- 是否产生候选
- 模型是否调用 `choose_sticker`
- 非候选 ID 是否被拒绝
- 同轮是否只允许一张
- 文本回复是否仍正常

当前工具调用成功后只会记录 pending，不会发图。

### Step 5：实现 Milestone D

顺序建议：

1. SQLite 冷却表与状态机
2. pending → success/rollback
3. OpenClaw 公开媒体发送能力验证
4. 本地图片可信路径验证
5. 文本与图片发送顺序
6. 微信官方 Bot 实机图片发送
7. 发送成功后 commit cooldown
8. 发送失败保持纯文本回复

## 7. 当前分支与合并策略

开发分支：

```text
feature/sticker-system-v2
```

当前不建议立即合并到 `main`，原因：

- 工具仍被 `tools.allow` 过滤
- V2 尚未进行真实候选和 Qwen 工具调用测试
- 图片发送与冷却未实现

适合在以下条件满足后创建 PR：

```text
22 项测试继续通过
choose_sticker 不再被工具策略移除
真实 Catalog 能生成候选
Qwen 至少完成一次合法 choose_sticker 调用
V1 微信文字回复仍正常
```

## 8. 本地工作区注意事项

之前切换 V2 分支时，本地以下文件存在未提交改动：

```text
integrations/openclaw/index.ts
scripts/install-openclaw-adapter
scripts/test-openclaw-adapter
```

这些改动已经使用 stash 保存。继续开发前先检查：

```bash
git stash list
git stash show -p stash@{0}
```

暂时不要直接 `git stash pop`，因为 V2 已经修改了相同文件，直接恢复可能产生冲突。

如确认 stash 内容已被 V2 正式代码取代，可在人工核对后再删除对应 stash。

## 9. 快速恢复工作环境

```bash
cd ~/Yogurt_Chat_Bot

git fetch origin
git switch feature/sticker-system-v2
git pull --ff-only origin feature/sticker-system-v2

bash scripts/test-openclaw-adapter

openclaw config validate
openclaw plugins doctor
openclaw plugins inspect yogurt-social-injector --runtime --json
openclaw gateway status --deep --require-rpc
```

当前最先要处理的命令：

```bash
openclaw config get tools.allow
```

## 10. 相关文档

```text
docs/V2_DESIGN.md
docs/V2_PROGRESS.md
integrations/openclaw/README.md
assets/stickers/README.md
social_layer/config/stickers.example.json
```

独立分类器：

```text
Doman1c/Yogurt_Sticker_Tagger
README.md
schemas/sticker-catalog-v1.schema.json
```
