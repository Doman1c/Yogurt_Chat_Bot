# Yogurt Social Layer V1

V1 只解决三个问题：

1. 判断当前消息属于什么场景；
2. 根据场景决定本轮应该怎样说话；
3. 只检索与当前话题直接相关的用户记忆和共同经历。

它不会替换 OpenClaw，也不会额外调用一次大模型。V1 生成一段简短的
`Yogurt Social State`，后续可以在 OpenClaw 调用主模型前注入上下文。

## 同步路径

```text
微信消息
  → StyleRouter 规则分类
  → SQLite 检索相关记忆
  → 生成 Social State
  → OpenClaw 主模型生成最终回复
```

## 场景

- `technical`：配置、代码、报错、部署等技术问题
- `casual`：普通聊天
- `playful`：玩笑、轻微调侃、表情包语境
- `comfort`：难过、委屈、孤独、感情问题
- `celebration`：成功、完成、连接成功等
- `serious`：签证、医疗、安全、法律等需要谨慎回答的内容

情绪类信号优先于技术关键词。例如：

```text
模型又报错了，我真的很难过
```

会优先路由到 `comfort`，避免只回答技术故障而忽略用户情绪。

## 记忆结构

V1 使用 Python 标准库 SQLite，分为：

- `users`：用户和关系级别
- `facts`：稳定事实
- `preferences`：喜欢与不喜欢的表达方式
- `episodes`：共同经历

V1 不会从聊天中自动写入记忆。自动学习将在 V3 中先写入候选区，审核后
再晋升为正式记忆，避免错误内容污染人格。

## 本地运行

在仓库根目录：

```bash
bash scripts/yogurt-social upsert-user \
  --user-id owner \
  --display-name Domanic \
  --relationship owner

bash scripts/yogurt-social remember-preference \
  --user-id owner \
  --category 回答风格 \
  --value 客服式回答 \
  --dislike

bash scripts/yogurt-social remember-episode \
  --user-id owner \
  --summary "我们一起建立了 Yogurt Chat Bot 的 GitHub 私有仓库" \
  --importance 0.9

bash scripts/yogurt-social analyze \
  --user-id owner \
  --message "OpenClaw 配置报错了，怎么修复？"
```

只输出可注入的上下文：

```bash
bash scripts/yogurt-social analyze \
  --user-id owner \
  --message "今天终于连接成功了" \
  --context-only
```

运行测试：

```bash
PYTHONPATH=social_layer/src python3 -m unittest discover \
  -s social_layer/tests -v
```

## V1 集成边界

当前提交完成的是可运行的社交层核心。下一小步是确认 OpenClaw 中最稳定的
前置注入点，再添加一个很薄的适配器。适配器只负责：

1. 接收 `user_id` 与当前消息；
2. 调用 `SocialLayerService.analyze()`；
3. 把 `context_block` 附加到本轮系统上下文；
4. 回复完成后不做任何自动写入。
