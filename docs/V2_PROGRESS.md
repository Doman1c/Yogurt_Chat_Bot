# Yogurt V2 开发进度

更新时间：2026-08-01  
开发分支：`feature/sticker-system-v2`

## 已完成

### Milestone A：独立 Sticker Tagger

由独立仓库 `Doman1c/Yogurt_Sticker_Tagger` 提供：

- 图片扫描、校验与 SHA-256 去重；
- WEBP 归一化与缩略图；
- Ollama `qwen3.5:9b` 视觉分类；
- 严格结构化输出；
- pending/approved/rejected 审核；
- 只导出 approved 且 enabled 的 Catalog；
- 已通过真实图片分类。

### Milestone B：Catalog 与候选召回基础

- Chat Bot 直接消费 Tagger Catalog V1；
- 全局运行参数与图片 Catalog 分离；
- 安全 Catalog 加载；
- asset root 与图片路径逃逸拦截；
- pending、disabled、风险和关系不匹配过滤；
- scene、mood、keyword、relationship 加权召回；
- Top-K 候选输出；
- V1 `use_sticker=false` 可阻止候选。

### Milestone C：模型自主选择基础

- bridge 返回结构化候选；
- `before_prompt_build` 注入候选说明；
- manifest 声明 `choose_sticker`；
- 每轮候选快照绑定运行/会话/渠道身份；
- 工具只能选择当前候选 ID；
- 同一轮最多选择一张；
- 相同 ID 重复调用幂等；
- 候选过期和数量上限；
- 选择只记录 pending，不直接发送图片；
- V2 默认关闭，不改变 V1 生产行为。

## 尚未完成

### Milestone C 剩余

- 在真实 OpenClaw Gateway 中确认工具出现在运行时工具列表；
- 使用 Qwen 实测模型能否在合适场景自主调用；
- 调整候选提示长度和工具描述；
- 根据实测决定工具是否需要额外 allow 配置。

### Milestone D：冷却与微信发送

- SQLite 冷却状态；
- 全局、场景、单图和每小时限制；
- `reply_payload_sending` 或公开媒体发送能力；
- 本地可信媒体路径验证；
- 图片成功后 commit cooldown；
- 失败时 rollback pending；
- 文本成功、图片失败时保持正常回复；
- 微信官方 Bot 实机图片发送。

### Milestone E：管理体验

- watch 模式；
- 批量审核；
- 重分类；
- 统计与误标记录；
- 可选本地管理页面。

## 当前边界

当前 V2 代码不会发送任何图片。即使模型成功调用 `choose_sticker`，插件也只保存短期 pending decision。发送和冷却必须等 Milestone D 完成并通过实机验证后才能启用。
