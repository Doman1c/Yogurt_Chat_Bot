# Yogurt V4.1 Response Planner

V4.1 在 V4.0 结构化会话状态之上增加一个确定性的回复规划器。

## 目标

在不增加第二次模型推理的前提下，为一次正常 Qwen 推理提供本轮表达计划：

- 本轮应该回答、排查、继续、庆祝、安慰还是简短确认；
- 首要目标；
- 语气和建议长度；
- 是否确实需要追问；
- 建议气泡数；
- 表情倾向；
- 需要避免的重复行为。

V4.1 不修改 V3 的表情选择、多气泡发送和自动记忆逻辑。

## 数据流

```text
用户消息
  ↓
V4.0 ConversationStateRuntimeStore
  ├── 更新 topic / dialogue_act / mood / interaction_mode
  ├── 更新 open_threads / recent_actions
  └── 不保存原始消息
  ↓
V4.1 ResponsePlanner
  ├── 读取当前结构化状态
  ├── 生成 bounded ResponsePlan
  └── 生成 Prompt 白名单上下文
  ↓
OpenClaw before_prompt_build
  ↓
一次 Qwen 正常推理
  ↓
完整回复观察
  └── 更新 last_response_pattern
```

状态更新和规划使用同一次轻量 Python observer 调用，不新增第二个 observer 进程，也不调用额外模型。

## ResponsePlan 字段

```json
{
  "plan_version": "4.1",
  "dialogue_act": "plan",
  "primary_goal": "advance_current_project",
  "tone": ["natural", "collaborative", "technical", "direct"],
  "reply_length": "medium",
  "ask_followup": false,
  "bubble_target": 2,
  "sticker_preference": "neutral",
  "avoid": [
    "generic_followup_offer",
    "repeat_background",
    "unnecessary_question",
    "excessive_roleplay"
  ],
  "continuity_topic": "回复规划器",
  "active_thread": "实现 V4.1 回复规划器",
  "state_version": 1,
  "reasons": ["dialogue_act:plan", "project_planning"]
}
```

Planner 只输出枚举值和由本地 reducer 生成的结构化标签，不返回原始用户消息。

## Prompt 注入

启用后注入以下 bounded block：

```text
[Yogurt Response Plan V4.1]
本轮行为：plan
首要目标：推进当前项目的下一步
语气：自然、协作、技术清晰、直接
建议长度：中等
建议气泡数：2
主动追问：否
表情倾向：中性，由表情系统独立判断
连续话题：回复规划器
当前待办：实现 V4.1 回复规划器
避免：
- 不要重新介绍已经明确的项目背景
- 不要用“需要我继续吗”之类的机械式收尾
```

该上下文不会包含原始消息、模型回复、文件路径、账号 ID 或评分。

## 规划行为

### plan

- 推进当前项目；
- 协作、技术清晰；
- 默认中等长度；
- 不机械追问；
- 避免重复项目背景。

### troubleshoot

- 以定位和修复为目标；
- 允许详细回答；
- 技术内容只保留轻度角色语气；
- 已有日志时不重复索要同一信息。

### celebrate

- 认可刚完成的成果；
- 简短、温暖；
- 表情倾向为 celebration；
- 不追加无关建议。

### comfort

- 先回应感受；
- 表情倾向为 comfort；
- 避免过度玩笑和机械建议。

### acknowledge

- 很短；
- 不重新解释背景；
- 不主动追问；
- 不建议发表情。

### clarify

只有当引用对象确实缺失时，允许询问一项必要信息。普通“怎么做”问题仍视为直接回答，而不是反问用户。

## 重复抑制

Planner 会读取 `last_response_pattern`：

```text
technical_long
explanatory_medium
multi_paragraph
```

上一轮较长时，后续 continue / acknowledge / plan 会加入：

```text
repeat_long_summary
```

并优先缩短回复。

## 配置

```json
{
  "conversationStateEnabled": false,
  "responsePlannerEnabled": false,
  "responsePlannerMaxContextChars": 1600
}
```

`responsePlannerEnabled=true` 时会自动维护 Planner 所需的会话状态，即使 `conversationStateEnabled=false`。

两个开关的区别：

- `conversationStateEnabled`：启用 V4 状态观察和日志；
- `responsePlannerEnabled`：生成并注入 V4.1 回复计划。

两项默认均关闭。

## Fail-open

以下情况不会阻断聊天：

- Python observer 无法启动；
- SQLite 写入失败；
- Planner 生成失败；
- 返回内容缺失或 Marker 不正确；
- Context 超过上限。

失败时不注入 Planner，原有 V3 文本链路继续运行。

## 测试

```bash
bash scripts/test-v4-state
bash scripts/test-v4-runtime
```

测试覆盖：

- V4.1 topic 和 open thread；
- plan / celebrate / comfort / clarify / acknowledge；
- 完成任务后关闭 V4.1 thread；
- V4.0 与 V4.1 action 不串阶段；
- 长回复重复抑制；
- Context 不包含原始消息；
- Planner 注入先于模型推理；
- 完整回复只观察一次；
- 多气泡子消息不重复写状态。

## 实机验收

启用 Planner 后，日志应出现：

```text
yogurt-social-injector: v4 response plan ready act=plan goal=advance_current_project length=medium bubbles=2 followup=false sticker=neutral
```

同一轮仍应只有：

```text
phase=user      一次
phase=assistant 一次
```

关闭 `responsePlannerEnabled` 后，回复计划不再注入；V3 和 V4.0 可继续独立运行。
