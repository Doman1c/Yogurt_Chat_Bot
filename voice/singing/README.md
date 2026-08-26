# Yogurt Singing Runtime

Engine: [Seed-VC](https://github.com/Plachtaa/seed-vc), pinned to `51383efd921027683c89e5348211d93ff12ac2a8` for the verified Windows RTX 4080 baseline.

The repository provides a configuration-only preflight and a bounded inference launcher. It does **not** contain Seed-VC itself, model checkpoints, BigVGAN, Hugging Face cache, target references, source songs, stems, training data, or generated audio.

## Local setup

Copy `configs/runtime.example.json` to a location outside Git, replace every placeholder with local paths, and run:

```powershell
python .\voice\singing\scripts\preflight.py --config C:\local\yogurt-singing.json
python .\voice\singing\scripts\run_seed_vc.py `
  --config C:\local\yogurt-singing.json `
  --source C:\local\source-vocals.wav `
  --target C:\local\yogurt-reference.wav
```

The wrapper invokes the separately installed upstream `inference.py` in offline Hugging Face mode and bounds diffusion steps to 1–100. See [runtime-baseline.md](docs/runtime-baseline.md) for the measured 4080 baseline.

## Singing Pipeline v2.1

Pipeline v2 adds a sequential, failure-isolated orchestration layer around the existing wrapper. It uses a separate pymss environment for CUDA RoFormer separation and never modifies the stable Seed-VC environment.

Copy `configs/pipeline.example.json` to a local-only `E:\Yogurt_Singing_Runtime\pipeline.json`, fill in the local Seed-VC runtime config and target reference, then put `.mp3`, `.wav`, `.flac`, or `.m4a` files in `E:\Yogurt_Singing_Runtime\incoming` and run:

```powershell
E:\Yogurt_Chat_Bot\voice\singing\scripts\start_singing_pipeline.ps1
```

Use `--Resume` to continue a verified separation or Seed-VC stage. Every job writes an output-local `job_manifest.json`; failed jobs retain their work and `logs\pipeline.log` in `E:\Yogurt_Singing_Runtime\failed\<job_id>`.

### Harmony-aware vocal conversion

V2.1 is opt-in. If the local-only pipeline configuration has no `harmony`
object, the prior V2 path is retained unchanged: `vocals_for_seedvc.wav` goes
to Seed-VC and the resulting vocal is mixed with the instrumental.

With the example `harmony` object enabled, the pipeline first uses the
configured local pymss model to split the already-isolated
`stems\vocals_for_seedvc.wav` into `stems\lead_vocals.wav` and
`stems\backing_vocals.wav`. Only `lead_vocals.wav` is copied into Seed-VC.
`backing_vocals.wav` never enters Seed-VC:

- `"mode": "preserve"` mixes it back in at `backing_gain_offset_db` (the
  recommended starting value is `-3.0`).
- `"mode": "remove"` omits it from the final mix.

`convert` is intentionally not a supported mode. A fresh harmony-enabled job
fails closed if its harmony split cannot be produced or validated; it does not
silently send mixed vocals to Seed-VC. `--skip-harmony-separation` is only a
debug guard and therefore also causes a fresh harmony-enabled job to fail.

Provision the selected separator model in the local `separation.model_dir`
before enabling it. The checked example uses the pymss catalog model
`UVR-BVE-4B_SN-44100-1`, whose BVE output is mapped as `Instrumental` = lead
residual and `Vocals` = backing extract. Other separator models must declare
both `lead_stem_tokens` and `backing_stem_tokens` in the `harmony` object.

Each harmony-enabled manifest records the separator engine/model, mode,
backing gain offset, lead/backing metrics, `no_backing_detected`, and that the
Seed-VC input type was `lead_only`. `--Resume` validates and reuses existing
lead/backing stems when available, then reuses later verified Seed-VC output
as usual.

### Seed-VC artifact diagnostics

`scripts/diagnose_seedvc_artifacts.py` is a bounded diagnostic for an existing
job. It reuses its separated vocal stem and the existing `run_seed_vc.py`
wrapper; it does not run source separation or a whole-song conversion. Provide
only local paths through a local-only pipeline config, for example:

```powershell
python .\voice\singing\scripts\diagnose_seedvc_artifacts.py `
  --job-root "E:\path\to\outputs\job-id" `
  --config "C:\local\yogurt-singing-pipeline.json" `
  --ranges "18:26,101:109,116:125,160:170" `
  --steps "30,40,50"
```

It writes source/current conversions, each step variation, comparisons, source
boundary calculations, and optional F0 diagnostics beneath the job's
`diagnostics` directory. Use `--labels` when the output folders should use
specific artifact identifiers rather than their window start times.
