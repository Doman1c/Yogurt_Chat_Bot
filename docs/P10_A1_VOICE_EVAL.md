# P10-A1 Voice Evaluation

日期：2026-08-24

## 结论

P10-A1 的基础链路已经验证可运行：本地 Fun-CosyVoice3 可在 Mac 上启动，能够读取本地日语参考音频并生成本地 WAV，整个 TTS 过程不经过云端。

但本轮实听也确认：当前 CosyVoice3 zero-shot / cross-lingual 路线暂时不适合作为酸奶中文声线的最终方案。下一阶段转入 P10-A2 speaker adaptation / few-shot 训练验证，同时保留 Voice Runtime 的引擎适配器边界。

## 实测结果

### 1. 日语 reference -> 日语 zero-shot

使用真实 prompt text + prompt wav 的 `inference_zero_shot()` 后：

- 目标音色：通过，明显接近参考声线；
- 温柔语气 / prosody：通过；
- 自然度：明显优于此前 cross-lingual / instruct 测试；
- 已确认 prompt text 与 prompt wav 同时参与时，参考语气能被更稳定地保留。

### 2. 日语 reference -> 中文 zero-shot

- 音色：基本可保留；
- 中文音调：不自然；
- 咬字：不够清晰；
- 跨语言生成后，中文韵律明显弱于同语言日语结果。

结论：speaker identity 可迁移，但当前跨语言 prosody / pronunciation 质量未达到酸奶正式声线要求。

### 3. `inference_instruct2()` 风格控制

- 可以把整体听感拉向更温柔的方向；
- 但出现明显 bot 感、电子感 / 电流感和声学伪影；
- 不作为当前正式路线。

### 4. 中文 source TTS -> CosyVoice VC

尝试使用系统中文 TTS 作为 source，再通过 `inference_vc()` 转为目标音色：

- 中文咬字：清晰；
- 情感 / prosody：过于机械；
- 经过 VC 后电子感和转换伪影进一步明显；
- 整体听感不符合角色陪伴型语音目标。

结论：停止继续优化 macOS 系统 TTS -> VC 方案。

## BGM 问题

目前效果较好的参考片段仍含背景音乐。后续正式训练前，必须先使用高质量 source separation 工具清理人声，再进行切句和转写。参考音频、训练数据和模型权重全部保持本地，不进入 Git。

## P10-A2 决策

P10-A2 改为验证 few-shot speaker adaptation 路线，首个训练候选为 GPT-SoVITS。

目标流程：

```text
约 5 分钟本地日语单人素材
    ↓
人声 / BGM 分离
    ↓
VAD / 切句
    ↓
日语 ASR + 人工校对
    ↓
本地私有训练集
    ↓
Windows CUDA GPU few-shot / speaker adaptation
    ↓
固定中文测试集验收
    ↓
选定 checkpoint
    ↓
通过 Voice Runtime engine adapter 接入
```

### P10-A2 验收条件

1. 训练数据全部本地处理，reference / dataset / checkpoint 不提交 Git；
2. 完成一次可复现的 few-shot 训练；
3. 固定中文测试句的咬字、声调和自然度明显优于本轮 CosyVoice cross-lingual；
4. 音色相似度不低于本轮表现较好的日语 zero-shot；
5. 不出现明显持续的 bot / 电流 /金属伪影；
6. 上层仍只依赖 typed Voice Runtime API，不让 Chat Bot 绑定具体 TTS 实现。

## 架构不变项

本轮引擎选择变化不改变 P10 总体安全边界：

- Voice Runtime 继续只监听 loopback；
- Browser / Cloud 不直接访问 TTS、reference、dataset 或模型权重；
- 不新增 Cloud -> Mac inbound；
- 不提供 shell、generic command bus、任意路径或任意 endpoint 执行；
- OpenClaw / qwen 只负责“说什么”，Voice Runtime 负责“怎么说”。

## 下一步

下一工作日从数据准备开始：先把完整约 5 分钟素材做高质量人声分离，再自动切句、转写和校对；数据集通过后才开始 GPU 训练。
