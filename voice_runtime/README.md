# Yogurt Voice Runtime

P10 的本地语音运行时目录。该层与 OpenClaw / `qwen3.5:9b` 解耦，TTS / VC 引擎必须通过 adapter 接入，避免上层绑定具体模型实现。

当前阶段：P10-A1 本地链路已完成实机验证，P10-A2 转入 few-shot speaker adaptation 验证。

## Runtime boundary

- 固定 loopback：`127.0.0.1:8770`
- 只允许 typed API；不提供 shell / arbitrary path / generic command。
- Voice Profile、reference audio、training dataset、model weights、generated audio cache 均为本地私有资产。
- Cloud / Browser 不直接访问本服务。

## P10-A1 结果

Fun-CosyVoice3 已完成本地 proof：

```text
Japanese reference wav
+
Japanese / Chinese target text
->
local WAV
```

实听结论：同语言日语 zero-shot 可以获得较好的目标音色和自然语气；跨语言中文能够保留音色，但中文音调、咬字和自然度未达到正式使用要求；`inference_instruct2()` 与系统中文 TTS -> VC 方案均出现明显 bot / 电流 / 声学伪影。

详细记录见 `docs/P10_A1_VOICE_EVAL.md`。

## P10-A2 当前方向

下一阶段优先验证 few-shot speaker adaptation，首个训练候选为 GPT-SoVITS：

```text
local voice material
-> source separation
-> segmentation / transcript alignment
-> private training dataset
-> GPU speaker adaptation
-> Chinese acceptance set
-> selected checkpoint
-> Voice Runtime adapter
```

CosyVoice3 仍保留为可替换的实验 adapter，不再作为当前中文正式声线的唯一目标。

后续实现以 `docs/P10_VOICE_RUNTIME.md` 与 `docs/P10_A1_VOICE_EVAL.md` 为准。
