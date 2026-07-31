# Yogurt Sticker Tagger

Yogurt Sticker Tagger 是 V2 的独立表情包/照片自动分类程序。

它不依赖 OpenClaw Gateway，也不参与每轮聊天。它只负责把新图片整理成带有结构化标签的正式图片库，供聊天运行时读取。

## 计划中的工作模式

### 单次扫描

```bash
python3 -m yogurt_sticker_tagger scan \
  --input ../../assets/stickers/inbox \
  --output ../../assets/stickers/images \
  --catalog ../../assets/stickers/stickers.json
```

### 预览但不写入

```bash
python3 -m yogurt_sticker_tagger scan --dry-run
```

### 监听目录

```bash
python3 -m yogurt_sticker_tagger watch
```

## 处理流程

```text
扫描新图片
→ 格式检查
→ SHA-256 去重
→ 图片归一化
→ VLM 分类与 OCR
→ Schema 校验
→ 写入 pending/approved 记录
→ 更新 catalog
```

## 设计边界

Sticker Tagger：

- 可以使用独立的本地或远程视觉模型
- 可以批量处理图片
- 可以在聊天机器人离线时运行
- 不发送微信消息
- 不读取用户聊天记录
- 不决定某轮聊天最终发送哪张图

聊天运行时：

- 不临时调用视觉模型
- 只读取已分类、已批准图片
- 把少量候选交给主聊天模型选择
- 强制执行冷却和安全检查

## 目录

```text
assets/stickers/inbox/      新上传图片
assets/stickers/images/     正式归一化图片
assets/stickers/stickers.json
runtime/sticker_tagger/     任务、缓存与失败记录
```

## 分类输出

分类结果至少包含：

- 画面描述
- OCR 文本
- 场景标签
- 情绪标签
- 语气标签
- 关键词
- 关系适用范围
- 内容风险
- 模型和置信度
- 审核状态

完整字段见：

- `assets/stickers/stickers.schema.json`
- `docs/V2_DESIGN.md`

## Provider 设计

分类模型通过独立 Provider 接口配置，不和聊天模型绑定：

- `ollama_vlm`
- `openai_compatible_vision`
- `filename_rules`

初版会先实现统一接口和一个本地 Provider，再补充其他后端。
