# P10 Voice Runtime

P10 的目标是让酸奶拥有独立、可替换、可本地运行的声音层。LLM 继续负责“说什么”，Voice Runtime 只负责“怎么说”。

## 设计原则

- `qwen3.5:9b` / OpenClaw 不承担 TTS 推理；Voice Runtime 是独立模型与独立进程。
- 第一阶段只做本地 `text -> wav/pcm`，不改变微信正常文本链路。
- TTS / VC 必须通过 engine adapter 接入；上层不得绑定某个具体模型。
- Fun-CosyVoice 3.x 是 P10-A1 的首个实机候选；A1 已证明本地链路可运行，但中文跨语言质量未达到正式声线要求。
- P10-A2 当前转入 few-shot speaker adaptation，首个训练候选为 GPT-SoVITS；是否成为正式引擎由实机质量和 Mac 运行兼容性决定。
- Voice Profile、参考音频、训练数据、模型权重与生成缓存属于本地私有资产，不进入 Git。
- Browser / Cloud 不直连 TTS；未来 WebUI 只显示受限健康状态和安全配置摘要。
- 不新增 Cloud -> Mac inbound、generic command bus、任意命令或任意路径执行。

## P10 分阶段

### P10-A0 Foundation

- 定义 Voice Runtime 边界、固定 loopback API 和本地 Voice Profile 目录。
- 将模型实现与 Chat Bot 解耦。
- 明确本地私有资产忽略规则。

状态：完成。

### P10-A1 Zero-shot proof

原始验收目标：

```text
一段干净日语参考音频
+
任意中文文本
->
本地生成可播放 WAV
```

A1 已完成本地实机验证：

- Fun-CosyVoice3 可在 Mac 本地启动；
- 本地日语 reference 可参与生成；
- 中文 / 日语目标文本均可生成本地 WAV；
- 整个 TTS 过程不经过云端。

质量结论：

- 日语 reference + 准确 prompt text + 日语 zero-shot：音色、温柔语气和自然度明显较好；
- 日语 reference -> 中文 zero-shot：音色可保留，但中文音调、咬字和自然度不足；
- `inference_instruct2()` 能改变风格，但出现明显 bot / 电流 / 声学伪影；
- 系统中文 TTS -> CosyVoice VC 咬字清晰，但情感机械，且 VC 进一步叠加电子感。

因此 A1 的“基础链路验收”通过，但“中文正式声线质量门槛”未通过。详细记录见 `docs/P10_A1_VOICE_EVAL.md`。

### P10-A2 Yogurt Voice Profile v1 / speaker adaptation

P10-A2 不再继续堆叠 cross-lingual prompt 补丁，优先验证 few-shot speaker adaptation。

当前候选流程：

```text
约 5 分钟本地日语单人素材
    ↓
高质量人声 / BGM 分离
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
Voice Runtime engine adapter
```

首个训练候选：GPT-SoVITS。

如果训练结果达标，再整理正式本地 Voice Profile，例如：

```text
voices/yogurt/
├── profile.toml
├── references/
│   ├── neutral.wav
│   ├── happy.wav
│   ├── soft.wav
│   ├── shy.wav
│   └── angry.wav
├── models/
└── private_notes/
```

这些文件全部保持本地，不提交仓库。

A2 验收重点：

1. 固定中文测试句的声调、咬字和自然度明显优于 A1 cross-lingual；
2. 音色相似度不低于 A1 表现较好的日语 zero-shot；
3. 不出现明显持续的 bot / 电流 / 金属伪影；
4. 数据集、reference、checkpoint 全部本地；
5. 上层仍只依赖 Voice Runtime adapter，不感知具体训练框架。

### P10-A3 Local TTS service

固定 loopback 服务：

```text
127.0.0.1:8770
```

建议只暴露 typed API：

```text
GET  /health
POST /v1/speech
```

`POST /v1/speech` 的逻辑请求：

```json
{
  "text": "主人，你回来啦。",
  "voice": "yogurt",
  "emotion": "happy",
  "intensity": 0.7,
  "speed": 1.0,
  "stream": false
}
```

第一版允许 `emotion` 仅作为受限 hint；不要求引擎必须实现全部情绪。

### P10-B Voice Orchestrator

OpenClaw / Qwen 输出最终正文后，由独立 Orchestrator 组装 VoiceRequest：

```text
final text
+
scene / mood / planner hints
->
VoiceRequest
->
Local Voice Runtime
```

Qwen 仍只进行正常聊天所需的模型推理；TTS 不把音频重新塞回 LLM context。

### P10-C Streaming + playback

目标：从一次性 WAV 演进到低延迟流式 PCM，支持：

- Mac 本地播放；
- 后续 Live2D lip sync；
- 后续桌面 Companion；
- 后续微信语音消息（若官方通道能力允许）。

### P10-D Expressive voice

在固定声线稳定后，再增加：

- neutral
- happy
- shy
- sad
- angry
- surprised
- soft

情绪由 bounded enum / intensity 控制，不允许模型生成任意 TTS 指令或任意路径。

### P10-E Ears / STT（后续）

STT 与 TTS 分开：

```text
Mic -> VAD -> STT -> text -> Qwen -> text -> TTS -> audio
```

STT 文本才进入 16k context；PCM/WAV 不进入 LLM context。

## 运行架构

```text
                       Mac Private Core

User / WeChat
     |
     v
 OpenClaw
     |
     v
 qwen3.5:9b  ------------------------------+
 "what to say"                            |
     | final text                           |
     v                                      |
 Voice Orchestrator                         |
     |                                      |
     | typed VoiceRequest                   |
     v                                      |
 Yogurt Voice Runtime                       |
 127.0.0.1:8770                             |
     |                                      |
     +-- Text normalization                 |
     +-- Voice Profile: yogurt              |
     +-- TTS Engine Adapter                 |
     |      +-- GPT-SoVITS (A2 candidate)   |
     |      +-- CosyVoice3 (A1 prototype)   |
     |      +-- future engine               |
     |                                      |
     v                                      |
 PCM / WAV                                  |
     |                                      |
     +--> Mac Speaker                       |
     +--> future Live2D mouth               |
     +--> future voice message              |

Cloud Control Plane <----- bounded status only
(no direct access to TTS / reference audio / weights)
```

## Training / adaptation architecture

```text
Authorized target voice recordings
        |
        v
Audio cleanup / source separation / segment / transcript alignment
        |
        v
Local private dataset
        |
        +--> zero-shot reference set --------+
        |                                    |
        +--> optional speaker adaptation     |
                                             v
                                    Yogurt Voice Profile
                                             |
                                             v
                                    Local TTS Runtime
```

训练可以在独立 CUDA GPU 主机上完成；训练主机不因此成为酸奶常驻控制面，也不改变 Mac-only outbound 的安全边界。训练产物拷回本地运行前，需要单独验证目标引擎在 Mac 上的兼容性、内存和延迟；不提前假设训练框架一定适合长期在 M1 上推理。

## Voice data rules

推荐参考 / 训练数据：

- 单人、干净、无 BGM、无其他人声；
- zero-shot 可先准备 3~5 段、每段约 5~15 秒；
- speaker adaptation 优先使用更多高质量、完整语句，而不是把长音频整段直接塞入训练；
- 每段保留准确原文转写；
- 优先覆盖 neutral / happy / soft / shy / angry 等典型风格；
- 真实人物或可识别配音演员的声音仅在拥有相应授权时做一比一声音克隆。

## M1 16 GB 资源策略

P10 不假设 Brain 与 TTS 永久同时常驻。正式 Runtime 候选需要记录：

- Voice Runtime 模型加载内存；
- cold start；
- 首包/整句延迟；
- 与 `qwen3.5:9b` 同时驻留时的系统内存压力。

若内存压力较高，优先设计 warm/unload 策略，而不是降低 Brain context 或牺牲 TTS 音质。

## P10-A1 状态

A1 基础验收四项均已满足：

1. 受控 CosyVoice3 引擎可在本机启动；
2. 使用本地日语 reference voice；
3. 可输入中文文本；
4. 可生成本地 WAV，且不经过云端 TTS。

但 A1 的实听质量不足以封为酸奶正式中文声线，因此 P10 当前进入 A2 speaker adaptation / few-shot 训练验证。
