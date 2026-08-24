# P10-S3 — Offline Runtime Preflight

This is a hard gate before a real engine process starts. It performs no model
download, no GPU inference, and no training.

## Common provenance gate

Each trusted runtime configuration supplies a full 40-character source SHA.
The adapter runs fixed `git -C <trusted-working-directory> rev-parse HEAD` and
`git status --porcelain --untracked-files=no`. A mismatch, missing Git, Git
failure, or tracked source modification is bounded as unavailable and blocks
execution. Ignored/untracked model cache material does not make source dirty.

`BakeoffRunManifest.engine_revision` is populated only from this verified
revision—not an unchecked TOML value.

## Seed-VC offline gate

Pinned `inference.py` calls its `hf_utils.load_custom_model_from_hf` for RMVPE
and CAMPPlus, `BigVGAN.from_pretrained` for its vocoder, and Transformers
`WhisperModel.from_pretrained` / `AutoFeatureExtractor.from_pretrained` for
the selected 44.1 kHz F0 config. The selected DiT checkpoint/config alone is
therefore insufficient.

The private config must declare present local cache/model locations for all of:

- `rmvpe`;
- `campplus`;
- `bigvgan`;
- `whisper`.

The child receives process-local `HF_HUB_OFFLINE=1`,
`TRANSFORMERS_OFFLINE=1`, and `HF_DATASETS_OFFLINE=1`. It otherwise inherits
the host environment, including PATH, SYSTEMROOT, and CUDA variables. Missing
dependencies produce bounded `engine_unavailable`; they cannot trigger a
silent transfer. No global environment or firewall setting is changed.

## Demucs offline gate

Demucs requires a trusted local cache/model dependency for its selected
`htdemucs` or `htdemucs_ft` model. The adapter rejects a missing runtime/model
before running `python -m demucs.separate`; its process also receives the same
offline policy. Missing runtime/model maps to `separator_unavailable`, process
exit or missing stems maps to `separation_failed`, and timeout/cancellation
preserve their bounded codes.

## Safe Windows readiness command

After cloning the exact source revision, preparing every dependency locally,
and copying the example into ignored `singing_runtime/config.toml`, run:

```powershell
Set-Location 'C:\path\to\Yogurt_Chat_Bot'
& 'E:\Yogurt_Singing_Runtime\envs\seedvc\Scripts\python.exe' -m capabilities.singing.bakeoff_cli `
  --sample-id zh-proto --engine seed_vc --language zh --transpose 0 --preflight-only
```

It prints only bounded JSON: `READY` plus verified revision, or a bounded
error. It never prints private paths, config content, raw stderr, or a Python
traceback. Only `READY` authorizes the separate short first-proof command.
