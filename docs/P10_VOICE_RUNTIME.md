# P10 Voice Runtime

P10 的目标是让酸奶拥有独立、可替换、可本地运行的声音层。LLM 继续负责“说什么”，Voice Runtime 只负责“怎么说”。

## 设计原则

- `qwen3.5:9b` / OpenClaw 不承担 TTS 推理；Voice Runtime 是独立模型与独立进程。
- 第一阶段只做本地 `text -> wav/pcm`，不改变微信正常文本链路。
- 优先使用 Fun-CosyVoice 3.x 的 zero-shot / cross-lingual voice cloning 作为首个引擎候选。
- 目标参考音频可以是日语；中文发音能力由多语种基础 TTS 提供，参考音频主要提供 speaker identity / timbre / prosody 条件。
- Voice Profile、参考音频、模型权重与生成缓存属于本地私有资产，不进入 Git。
- Browser / Cloud 不直连 TTS；未来 WebUI 只显示受限健康状态和安全配置摘要。
- 不新增 Cloud -> Mac inbound、generic command bus、任意命令或任意路径执行。

## P10 分阶段

### P10-A0 Foundation

- 定义 Voice Runtime 边界、固定 loopback API 和本地 Voice Profile 目录。
- 将模型实现与 Chat Bot 解耦。
- 明确本地私有资产忽略规则。

### P10-A1 Zero-shot proof

验收目标：

```text
一段干净日语参考音频
+
任意中文文本
->
本地生成自然、可辨识目标音色的 WAV
```

第一阶段不做 fine-tune。先验证跨语言 zero-shot 的音色相似度、中文自然度和 M1 16 GB 可运行性。

### P10-A2 Yogurt Voice Profile v1

若 zero-shot 已足够满意，将高质量参考音频整理为本地 Voice Profile：

```text
voices/yogurt/
├── profile.toml
├── references/
│   ├── neutral.wav
│   ├── happy.wav
│   ├── soft.wav
│   ├── shy.wav
│   └── angry.wav
└── private_notes/
```

这些文件全部保持本地，不提交仓库。

若 zero-shot 音色或中文韵律不够，再进入 speaker adaptation / fine-tune，而不是从零训练整个 TTS。

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
     |      +-- CosyVoice3 (first target)   |
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
Audio cleanup / segment / transcript alignment
        |
        v
Local private dataset
        |
        +--> zero-shot reference set ----+
        |                                |
        +--> optional speaker adaptation |
                                         v
                                Yogurt Voice Profile
                                         |
                                         v
                                Local TTS Runtime
```

## Voice data rules

推荐参考数据：

- 单人、干净、无 BGM、无其他人声；
- 先准备 3~5 段、每段约 5~15 秒做 zero-shot；
- 如果后续要适配，再整理更多高质量片段；
- 每段保留准确原文转写；
- 优先覆盖 neutral / happy / soft / shy / angry 等典型风格；
- 真实人物或可识别配音演员的声音仅在拥有相应授权时做一比一声音克隆。

## M1 16 GB 资源策略

P10 不假设 Brain 与 TTS 永久同时常驻。首轮实机验证记录：

- Voice Runtime 模型加载内存；
- cold start；
- 首包/整句延迟；
- 与 `qwen3.5:9b` 同时驻留时的系统内存压力。

若内存压力较高，优先设计 warm/unload 策略，而不是降低 Brain context 或牺牲 TTS 音质。

## P10-A1 验收

P10-A1 只要求四件事：

1. 官方/受控 CosyVoice3 引擎可在本机启动；
2. 使用本地日语 reference voice；
3. 输入一段中文文本；
4. 生成可播放的本地 WAV，且不经过云端 TTS。

通过后再开始正式整理 Yogurt Voice Profile。
