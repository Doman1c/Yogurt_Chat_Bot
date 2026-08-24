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

Before installing on Mac, record the exact GPT-SoVITS Git commit used by the working Windows inference environment. Mac should begin from the same upstream revision unless a Mac-specific compatibility fix requires a documented deviation.

### B1-1 Install inference environment on Apple silicon

Install GPT-SoVITS in a separate Mac environment using the project's Apple-silicon / MPS path. Do not use the Mac as the primary training host.

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
- Next action: capture the exact working Windows GPT-SoVITS commit SHA, then install the matching revision on Mac.
