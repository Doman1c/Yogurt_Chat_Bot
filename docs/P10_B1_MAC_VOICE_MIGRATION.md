# P10-B1 Mac-local Voice Runtime migration

P10-B1 changes the deployment target of the already-accepted Yogurt speech inference stack from Windows runtime to Mac-local runtime.

## Decision

The long-running production path should be local to the Mac:

```text
OpenClaw
  -> Voice Orchestrator
  -> 127.0.0.1:8770 Yogurt Voice Runtime
  -> 127.0.0.1:9880 GPT-SoVITS
  -> local audio
```

Windows RTX remains a training / adaptation host only. No LAN Voice Bridge is required for normal speech inference.

## Invariants

- GPT-SoVITS and Yogurt Voice Runtime remain loopback-only.
- No Cloud -> Mac inbound access is added.
- Browser / Cloud never call GPT-SoVITS or Voice Runtime directly.
- Training data, reference audio, GPT/SoVITS checkpoints, and local config remain private and are not committed to Git.
- OpenClaw text delivery must remain fail-open if voice generation fails.
- Windows remains the rollback reference until Mac real-runtime E2E passes.

## Migration plan

### B1-0 Pin source revision

Working Windows GPT-SoVITS upstream revision:

```text
48b1a0169a28582a8984402f82cf438d3bfa6aca
Fix Fun-ASR-Nano Transformers requirement (#2824)
```

The Windows tree also contains local compatibility / training patches, so Mac must start from a clean checkout of this exact upstream revision rather than copying the dirty Windows worktree. Mac-specific deviations, if any, must be documented narrowly.

### B1-1 Install inference environment on Apple silicon

Install GPT-SoVITS in a separate Mac environment using the project's Apple-silicon / MPS path. Do not use the Mac as the primary training host.

Mac baseline recorded for migration:

- architecture: `arm64`
- macOS: `26.5.2`
- conda: `26.5.3`
- available disk at start: about `313 GiB`

### B1-2 Transfer private inference assets

Transfer only the assets required for inference:

- `yogurt_asuna_v1-e15.ckpt`
- `yogurt_asuna_v1_e4_s60.pth`
- one primary reference WAV
- ten additional reference WAVs
- exact primary reference transcript

Do not delete or overwrite the working Windows copies.

### B1-3 GPT-SoVITS loopback proof

Start GPT-SoVITS on `127.0.0.1:9880` and verify a direct Chinese synthesis request with the accepted profile.

### B1-4 Yogurt Voice Runtime proof

Create a Mac-local ignored `voice_runtime/config.toml`, start `127.0.0.1:8770`, and verify:

- `/health` reports engine reachable;
- `/v1/speech` returns playable WAV;
- default Chinese speed remains `1.13`;
- private paths are not exposed in responses.

### B1-5 Resource acceptance

Measure Mac memory / swap and latency with both the normal Brain model and GPT-SoVITS available. Decide between:

- always-warm GPT-SoVITS; or
- bounded warm / idle-unload policy.

Do not reduce Brain context or TTS quality merely to keep both permanently resident.

### B1-6 OpenClaw final-output integration

Only after Mac-local voice E2E passes, connect the observation-only final-output hook to the local Voice Orchestrator. TTS failure must not block normal text delivery.

## Training boundary

Normal speech generation is inference only. Further GPU training is needed only when deliberately creating a new or improved voice checkpoint, additional singing models, or other adaptations. The Windows RTX host remains appropriate for that work.

## Current status

- P10-B0 bounded Orchestrator proof: Windows real-runtime accepted.
- P10-B1 deployment decision: Mac-local runtime selected.
- LAN Voice Bridge approach: abandoned; do not implement.
- B1-0 upstream revision pin: complete.
- Next action: create a clean Mac checkout at the pinned revision and begin the Apple-silicon inference environment install.
