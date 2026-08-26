# Yogurt Daily TTS v1

Engine: GPT-SoVITS v2Pro. The selected Daily Voice is trained with **80% Asuna Japanese and 20% Mandarin support**. Listening validation found clear Mandarin, retained Asuna/Yogurt Japanese identity, and normal English generalization.

Features: one resident CUDA process, local FastAPI (`/v1/tts`), native 32 kHz WAV, Chinese/Japanese/English/auto routing, a one-at-a-time GPU lock, and reference-based emotion routing. `neutral` is the only committed available profile; `happy`, `excited`, `soft`, `shy`, `upset`, `sad`, `serious`, and `comforting` must use locally reviewed reference audio before they are enabled.

## Setup

Install GPT-SoVITS separately at the tested v2Pro-compatible revision, then copy `.env.example` to a local `.env` and populate only local paths. No model weight, reference audio, transcript derived from private audio, or training data belongs in Git.

```powershell
Copy-Item .\voice\tts\.env.example .\voice\tts\.env
& .\voice\tts\scripts\start_yogurt_tts.ps1
```

The service binds to `127.0.0.1:17861` by default; interactive docs are at `/docs`. The general Chat Bot-facing bounded gateway remains [`voice_runtime`](../../voice_runtime/README.md).

## Provenance

- Model family: GPT-SoVITS v2Pro.
- Selected checkpoints: GPT e15 and SoVITS e8 from the verified 80:20 experiment (local-only).
- Native baseline: `top_k=15`, `top_p=1.0`, `temperature=1.0`, `speed=1.0`, `text_split_method=cut5`.

See [model.example.json](configs/model.example.json) for placeholders and [runtime/app.py](runtime/app.py) for the portable runtime wrapper.
