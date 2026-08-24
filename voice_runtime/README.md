# Yogurt Voice Runtime

P10 的本地语音运行时目录。该层与 OpenClaw / `qwen3.5:9b` 解耦，TTS 引擎通过 adapter 接入，避免上层绑定具体模型实现。

当前阶段：P10-A2 已完成 GPT-SoVITS speaker adaptation 实机验收，P10-A3 开始实现本地 typed TTS service。

## Runtime boundary

- 固定 loopback：`127.0.0.1:8770`
- 只允许 typed API；不提供 shell / arbitrary path / generic command。
- 下游 GPT-SoVITS 也必须固定在 loopback，例如 `127.0.0.1:9880`。
- Voice Profile、reference audio、training dataset、model weights、generated audio cache 均为本地私有资产。
- `config.toml` 为本地文件并被 `.gitignore` 忽略；仓库只保留脱敏示例。
- Cloud / Browser 不直接访问本服务。
- 第一版只返回整段音频，不开放 streaming。

## P10-A2 选定声线

实机 A/B 后，当前 production baseline 为：

```text
GPT:    yogurt_asuna_v1-e15.ckpt
SoVITS: yogurt_asuna_v1_e4_*.pth

Chinese default speed: 1.10
Japanese default speed: 1.02
```

SoVITS e8 保留为更强气息/相似度候选，但当前跨语言中文默认使用 e4。

## P10-A3 架构

```text
OpenClaw / future Voice Orchestrator
        |
        | typed VoiceRequest
        v
Yogurt Voice Runtime
127.0.0.1:8770
        |
        | fixed local profile + bounded parameters
        v
GPT-SoVITS adapter
        |
        | loopback HTTP only
        v
GPT-SoVITS official api.py
127.0.0.1:9880
        |
        v
WAV / audio bytes
```

GPT-SoVITS 的 reference path、reference transcript 和 extra references 只存在于本地 `config.toml`。调用方不能通过 `/v1/speech` 传入任意路径、模型或下游 endpoint。

## API

### `GET /health`

返回 Voice Runtime 与下游 GPT-SoVITS 的受限健康状态，不返回本地路径、参考文本或 checkpoint 内容。

### `POST /v1/speech`

```json
{
  "text": "主人，你回来啦。",
  "voice": "yogurt",
  "language": "zh",
  "emotion": "neutral",
  "intensity": 0.7,
  "speed": null,
  "stream": false
}
```

当前约束：

- `voice` 只允许 `yogurt`
- `language` 只允许 `zh` / `ja`
- `emotion` 是 bounded hint，A3 adapter 暂不改变 GPT-SoVITS 情绪参数
- `speed` 可省略；中文默认 1.10，日语默认 1.02
- `speed` 仅允许 0.80–1.30
- `stream=true` 会被拒绝
- 单进程同时只允许一个 TTS 请求进入下游引擎

## Windows 启动

先在 GPT-SoVITS 环境中启动官方本地 API，并显式指定已验收的 GPT / SoVITS 权重：

```powershell
cd E:\Yogurt_TTS_Train\GPT-SoVITS
python api.py `
  -g "GPT_weights_v2Pro/yogurt_asuna_v1-e15.ckpt" `
  -s "SoVITS_weights_v2Pro/yogurt_asuna_v1_e4_s60.pth" `
  -a 127.0.0.1 `
  -p 9880
```

实际 e4 文件名以本地生成结果为准。

然后准备 Voice Runtime 本地配置：

```powershell
Copy-Item .\voice_runtime\config.example.toml .\voice_runtime\config.toml
```

把 `reference_path`、`reference_text` 和 `extra_reference_paths` 替换成本地私有 Voice Profile；不要提交 `config.toml`。

安装 Runtime 依赖并启动：

```powershell
python -m pip install -r .\voice_runtime\requirements.txt
python -m voice_runtime.app
```

健康检查：

```powershell
Invoke-RestMethod http://127.0.0.1:8770/health
```

中文生成：

```powershell
$body = @{
  text = "主人，你回来啦。今天过得怎么样？"
  voice = "yogurt"
  language = "zh"
  emotion = "neutral"
  intensity = 0.7
  stream = $false
} | ConvertTo-Json

Invoke-WebRequest `
  -Uri http://127.0.0.1:8770/v1/speech `
  -Method POST `
  -ContentType "application/json" `
  -Body $body `
  -OutFile .\yogurt_test.wav
```

## Privacy / security notes

- Runtime 拒绝 `0.0.0.0`、LAN IP 或远端 engine URL。
- adapter 使用 `trust_env=False`，不经过系统 HTTP 代理访问 loopback GPT-SoVITS。
- 下游错误正文不会直接转发给调用方，避免本地路径泄漏。
- Browser / Cloud 不应直接访问 `:8770` 或 `:9880`。
- 后续 P10-B 由本地 Voice Orchestrator 组装 bounded `VoiceRequest`。
