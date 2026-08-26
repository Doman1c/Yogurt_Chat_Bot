# Verified Windows Singing Baseline

This is a performance record only. The checkpoint, reference, source clip, converted audio, cache, and full Seed-VC repository remain local-only.

| Item | Verified baseline |
|---|---|
| Engine | Seed-VC |
| Pinned commit | `51383efd921027683c89e5348211d93ff12ac2a8` |
| Python | 3.10.11 |
| PyTorch | 2.4.0+cu121 |
| GPU | NVIDIA GeForce RTX 4080 |
| CUDA | PASS |
| Preflight | READY |
| Test source duration | 28.184 s |
| Inference time | 11.077 s |
| RTF | 0.393 |
| Peak Torch VRAM | 2827.73 MiB |

The committed scripts require caller-provided local paths and run with Hugging Face offline mode enabled. They never download models or include media assets.
