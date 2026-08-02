# Yogurt V2 开发进度

更新时间：2026-08-02  
状态：**完成并通过微信实机验收**  
开发分支：`feature/sticker-system-v2`  
目标分支：`main`

## 最终版本

```text
Yogurt Chat Bot: V2.0
Yogurt Social Injector: 0.3.3
Yogurt Sticker Tagger: 0.2.0
Catalog Schema: 1.0
实机验收资产: 14 张 approved + enabled
```

## Milestone A：独立 Sticker Tagger

状态：完成。

独立仓库 `Doman1c/Yogurt_Sticker_Tagger` 已提供：

- PNG、JPG/JPEG、WEBP 扫描；
- 文件校验、SHA-256 去重和稳定 ID；
- WebP 归一化与缩略图；
- Ollama `qwen3.5:9b` 视觉分类；
- Prompt 1.1 中文场景与关键词策略；
- `content_warnings` 规范化与旧数据修复；
- pending / approved / rejected；
- enable / disable；
- 受约束编辑与不可变审核事件；
- 单项和批量重分类；
- 即时中文错误报告；
- 本地和令牌保护的远程 Web Admin；
- 上传与重分类进度；
- 原子导出正式 Catalog；
- Python 3.9～3.12 CI。

## Milestone B：Catalog 与安全召回

状态：完成。

- Chat Bot 直接消费 Tagger Catalog V1；
- 运行参数与图片 Catalog 分离；
- Schema、稳定 ID 和文件存在校验；
- asset root 和真实路径逃逸拦截；
- pending、disabled、风险和关系不匹配过滤；
- scene、mood、keyword、relationship 加权召回；
- Top-K 候选；
- 明确请求参数传入 Python Retriever；
- 明确请求安全 fallback；
- 仍严格要求 `approved + enabled + content_warnings=["none"]`。

## Milestone C：选择策略与单次推理

状态：完成。

### 默认 auto

- Retriever 输出候选；
- TypeScript 本地选择 Top 1；
- 明确请求使用较宽松阈值；
- 主动发送使用高阈值与 Top1/Top2 分差；
- `choose_sticker` 对模型隐藏；
- Qwen 只推理一次；
- 不向模型注入候选描述；
- 成功时只要求简短确认或颜文字；
- 无候选时禁止描述或虚构图片。

### tool 调试模式

- 候选快照绑定会话和渠道别名；
- 工具只能选择当前候选 ID；
- 相同 ID 幂等；
- 每轮最多一张；
- 候选过期和数量上限；
- 保留用于调试模型选择能力。

## Milestone D：冷却与微信发送

状态：完成并通过实机验收。

- 全局冷却；
- Scene 冷却；
- 单图冷却；
- 每小时数量上限；
- 路径、普通文件和大小验证；
- 发送成功后才提交冷却；
- 失败时保留文字；
- `agent_end` 主触发；
- `message_sent` 兼容回退；
- snapshot 一次性消费，避免重复发送；
- `api.runtime.channel.outbound.loadAdapter("openclaw-weixin")`；
- `adapter.sendMedia`；
- 微信 CDN 上传与图片消息；
- WebP 微信实机发送成功。

### 关键兼容结论

腾讯 `openclaw-weixin` 使用低层兼容 dispatcher，实机中未稳定触发第三方插件的 `reply_payload_sending`。因此 V2 `0.3.3` 使用独立 outbound adapter 发送图片。

非 bundled 插件必须设置：

```text
plugins.entries.yogurt-social-injector.hooks.allowConversationAccess=true
```

否则 `agent_end` 会被 OpenClaw 阻止。

## Milestone E：管理体验

状态：当前范围完成。

由 Tagger 提供：

- 批量上传；
- 上传进度；
- 标准图和缩略图预览；
- 状态筛选；
- 受约束字段编辑；
- 编辑后自动恢复 pending；
- 审批前保存未提交编辑；
- 重分类后台进度；
- 导出和审核事件；
- 远程令牌保护。

未来可选增强：

- watch 模式；
- 更丰富的统计与误标分析；
- Catalog 自动同步命令；
- 更多渠道适配器；
- 群聊和多用户策略。

## 微信实机验收结果

已通过：

```text
用户明确请求表情
→ candidates > 0
→ selected = sticker-...
→ 模型只回复简短文字
→ agent_end 调度
→ channel outbound adapter
→ 微信收到图片
```

以及：

```text
用户明确禁止表情
→ 只发送文字
```

验收日志关键字：

```text
stickerDecision=explicit
sticker outbound scheduled trigger=agent_end
sticker media sent trigger=channel_outbound_adapter
```

## 当前边界

- 官方微信 Bot 当前仅私聊；
- Chat Bot 不在聊天时调用视觉模型；
- 普通聊天不会自动写入长期记忆；
- 主动表情受阈值、分差和冷却限制；
- 真实图片、Catalog、SQLite 和凭据不提交 Git；
- 当前 14 张是验收时本地资产数量，不是代码中的固定上限。
