# Yogurt Voice Runtime

P10 的本地语音运行时目录。该层与 OpenClaw / `qwen3.5:9b` 解耦，首个目标引擎为 Fun-CosyVoice 3.x。

当前阶段：P10-A0 Foundation。

## Runtime boundary

- 固定 loopback：`127.0.0.1:8770`
- 只允许 typed API；不提供 shell / arbitrary path / generic command。
- Voice Profile、reference audio、model weights、generated audio cache 均为本地私有资产。
- Cloud / Browser 不直接访问本服务。

## First acceptance target

```text
Japanese reference wav
+
Chinese text
->
local WAV
```

在 zero-shot 效果未验证前，不做 fine-tune。

后续实现以 `docs/P10_VOICE_RUNTIME.md` 为准。
