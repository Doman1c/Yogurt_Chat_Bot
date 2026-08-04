# V4.0 Conversation State Engine

V4.0 是 Yogurt V4 的第一阶段，只负责记录和检查结构化短期对话状态。

它不会把 V4 状态注入模型 Prompt，因此开启后不应改变酸奶的可见回复内容。V4.1 才会在完成实机校准后引入 Response Planner。

## 数据范围

每条状态使用以下联合主键隔离：

```text
user_id + conversation_key
```

当前保存：

- `current_topic`
- `dialogue_act`
- `user_mood`
- `interaction_mode`
- `open_threads`
- `recent_actions`
- `last_response_pattern`
- 独立 TTL
- 状态版本和结构化变更审计

不会保存：

- 用户原始消息
- 酸奶原始回复
- 完整聊天记录
- 图片内容
- 模型思考过程

## 默认开关

```json
{
  "conversationStateEnabled": false,
  "conversationStateTimeoutMs": 2500,
  "conversationStateTopicTtlMinutes": 60,
  "conversationStateMoodTtlMinutes": 30,
  "conversationStateThreadTtlMinutes": 1440,
  "conversationStateMaxOpenThreads": 5,
  "conversationStateMaxRecentActions": 6
}
```

## 运行顺序

```text
用户消息
  ↓
V3 before_prompt_build 保持不变
  ↓
V4.0 observer 记录用户结构化状态
  ↓
单次 Qwen 推理
  ↓
V2.1 表情完整回复评分
  ↓
V4.0 observer 记录回复模式
  ↓
V3 多气泡拆分
```

完整回复对应的待处理状态只消费一次，因此子气泡不会重复写入 `assistant_turn`。

## 本地测试

```bash
bash scripts/test-v4-state
bash scripts/test-v4-runtime
```

## 开启观察模式

```bash
openclaw config set \
  'plugins.entries.yogurt-social-injector.config.conversationStateEnabled' \
  'true' \
  --strict-json

openclaw config validate
openclaw gateway restart
```

## 检查状态

列出 owner 的状态：

```bash
bash scripts/yogurt-state --pretty list --user-id owner
```

查看指定会话：

```bash
bash scripts/yogurt-state --pretty snapshot \
  --user-id owner \
  --conversation-key '<从 list 输出复制>'
```

查看结构化审计：

```bash
bash scripts/yogurt-state --pretty events \
  --user-id owner \
  --conversation-key '<从 list 输出复制>' \
  --limit 20
```

## 回滚

```bash
openclaw config set \
  'plugins.entries.yogurt-social-injector.config.conversationStateEnabled' \
  'false' \
  --strict-json

openclaw gateway restart
```

关闭后停止新增状态写入，但不会删除现有 SQLite 数据，也不会影响 V3 的记忆、多气泡或表情链路。
