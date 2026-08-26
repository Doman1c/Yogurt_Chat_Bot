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
