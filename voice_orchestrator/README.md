# Yogurt Voice Orchestrator

P10-B 的本地 Voice Orchestrator。它是独立 Yogurt Mouth capability 的内部依赖：接收已经确定的文本和受限 scene/mood，整理成 typed `SpeechRequest` 并调用 `127.0.0.1:8770` Voice Runtime。

## P10-B0 scope

当前第一步只做独立 proof，不观察聊天，也不改任何微信文本发送链路：

```text
Caller
        ↓
Mouth
        ↓
Voice Orchestrator
        ↓
typed SpeechRequest
        ↓
Yogurt Voice Runtime :8770
        ↓
WAV
```

约束：

- runtime URL 只能是 HTTP loopback；
- `voice` 固定为 `yogurt`；
- `language` 只允许 `zh` / `ja`；
- scene 只允许 `casual / technical / playful / comfort / celebration / serious`；
- mood 只允许 Voice Runtime 已定义的 bounded emotion；
- Orchestrator **不传 speed**，让 Voice Profile 的已验收默认值生效（中文 1.13）；
- P10-B0 不做 streaming，不让模型传本地路径、模型路径或任意 endpoint；
- 超过 A3 单请求上限的文本直接拒绝，不静默截断；后续 P10-C 再做安全分段/流式。

## Scene policy v0

```text
casual      -> neutral / 0.55
technical   -> neutral / 0.45
playful     -> happy   / 0.72
comfort     -> soft    / 0.55
celebration -> happy   / 0.82
serious     -> neutral / 0.50
```

`mood` 可以在 bounded enum 内覆盖 emotion，但 intensity 仍由 scene preset 决定。A3 的 GPT-SoVITS adapter 当前不会把 emotion 直接映射成任意底层控制，因此这层先建立稳定 contract，后续 P10-D 再接 expressive voice。

## Windows local proof

前提：

- GPT-SoVITS API 正在 `127.0.0.1:9880` 运行；
- Yogurt Voice Runtime 正在 `127.0.0.1:8770` 运行。

在仓库根目录执行：

```powershell
& E:\Yogurt_TTS_Train\envs\YogurtVoiceRuntime\python.exe `
  -m voice_orchestrator.cli `
  --text "主人，你回来啦。今天过得怎么样？" `
  --scene casual `
  --output E:\Yogurt_TTS_Train\yogurt_orchestrator_test.wav
```

预期输出包含：

```text
VOICE ORCHESTRATOR OK
scene = casual
emotion = neutral
speed = 1.130
```

这里的 `1.130` 来自 Voice Runtime 响应头，证明 Orchestrator 没有覆盖已验收的中文默认速度。

## 下一步

Voice Orchestrator 现在是 Mouth 的内部依赖：

```text
Caller
  ↓
Mouth
  ↓
Voice Orchestrator
  ↓
Voice Runtime
```

它不属于 WeChat，也不主动观察任何聊天 channel；输入必须是已经确定好的 text。未来调用者可以是 Native Chat、Desktop、Live2D 或 Robot。WeChat auto-TTS 保持 DEFERRED。
