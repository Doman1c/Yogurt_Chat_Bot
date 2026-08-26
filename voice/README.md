# Yogurt Voice

```text
Yogurt Voice
├─ TTS
│  ├─ GPT-SoVITS
│  ├─ Yogurt Daily TTS v1 (Asuna 80 / Mandarin 20)
│  └─ reference-based emotion routing
└─ Singing
   ├─ Seed-VC
   └─ local singing voice conversion
```

The Chat Bot remains the orchestration layer. Daily speech goes to the local TTS Runtime; a future singing request goes to the local Singing Runtime. Both are localhost-only, GPU-backed Windows services. This directory intentionally contains code, configuration examples, and deployment docs only—never checkpoints, reference audio, datasets, songs, outputs, caches, or credentials.

Existing `voice_runtime` and `voice_orchestrator` remain the bounded Chat Bot-facing gateway. The modules here document and provide reproducible deployment overlays for the verified Windows RTX 4080 voice engines.
