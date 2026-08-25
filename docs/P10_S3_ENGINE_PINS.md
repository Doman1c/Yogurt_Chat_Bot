# P10-S3 — Engine Pins

Checked 2026-08-25. Pins are source revisions, not a claim that a model has
been downloaded, installed, or exercised locally.

## Seed-VC

- Repository: <https://github.com/Plachtaa/seed-vc>
- Revision: `51383efd921027683c89e5348211d93ff12ac2a8` (`main` at check time).
- Archived: yes; GitHub reports the repository as archived. Do not treat it as
  a maintained production dependency.
- Source license: GPL-3.0.
- Selected first-proof SVC model:
  `DiT_seed_v2_uvit_whisper_base_f0_44k_bigvgan_pruned_ft_ema_v2.pth`, 44.1
  kHz, F0-conditioned Seed-VC V1 SVC path.
- Upstream's pinned `inference.py` default for F0 mode is the same
  `...ft_ema_v2.pth`. Earlier P10-S3 documentation named the older
  `...ft_ema.pth`; it is not selected for first proof and no A/B was run.
- Official model source: <https://huggingface.co/Plachta/Seed-VC>.
- Checkpoint size: 820,864,573 bytes (about 821 MB) for the selected SVC
  checkpoint. The model repository total is about 3.94 GB; inspect all
  transitive runtime downloads before downloading.
- Model terms: Hugging Face card reports GPL-3.0; confirm every fetched model
  and dependency term before distribution.
- Python: upstream suggests Python 3.10 on Windows, Apple Silicon Mac, or
  Linux. Use its separate `requirements.txt`/`requirements-mac.txt` only in
  the Seed-VC environment.
- PyTorch: upstream pins Torch/TorchVision/TorchAudio 2.4.0 in requirements;
  its file also contains a CUDA nightly index line, so resolve the Windows
  CUDA wheel deliberately rather than relying on an implicit install.
- CUDA: NVIDIA GPU recommended for practical performance; no VRAM claim is
  recorded until an authorized benchmark is run.
- Offline dependency inventory: selected DiT checkpoint/config plus RMVPE
  (`lj1995/VoiceConversionWebUI/rmvpe.pt`), CAMPPlus
  (`funasr/campplus/campplus_cn_common.bin`), BigVGAN
  (`nvidia/bigvgan_v2_44khz_128band_512x`), and Whisper Small
  (`openai/whisper-small`). The pinned upstream calls Hugging Face and
  `from_pretrained` for these at inference time; all must be explicitly cached
  and declared before P10-S3 permits process launch.
- Mac: upstream documents Apple Silicon support, but P10-S3 status is
  **UNKNOWN** without a real benchmark.
- Windows RTX 4080: expected first proof host; **READY FOR RUNTIME SETUP**.

## RVC

- Repository: <https://github.com/RVC-Project/Retrieval-based-Voice-Conversion-WebUI>
- Revision: `81eed5e8f68b6bed1789f682fe78cdd324495afc` (`main` at check time).
- Archived: no.
- Source license: MIT.
- Model/training format: local trusted `.pth` Yogurt Singer checkpoint and
  optional `.index`; neither is selected by public request fields.
- Official model source: <https://huggingface.co/lj1995/VoiceConversionWebUI>.
  Model size is not fixed because the Yogurt model does not yet exist. Inspect
  the official asset inventory before any download.
- Python: official current branch targets Python 3.12 x64.
- PyTorch/CUDA: for NVIDIA GPUs before RTX 50 series, upstream documents
  Torch/TorchAudio 2.7.1+cu118 followed by `requirments_cu118_py312.txt`.
- Windows RTX 4080: **LIKELY**, pending authorized dataset preflight and model
  setup. Mac: **UNKNOWN**; do not infer readiness from generic CPU/MPS support.

## Vevo2 / Amphion

- Repository: <https://github.com/open-mmlab/Amphion>
- Revision: `26f6883110181f1dbfe95c70a7c7dbaf4de5f42a` (`main` at check time).
- Archived: no.
- Source license: MIT.
- Inference entrypoint: `python -m models.svc.vevo2.infer_vevo2_fm` for
  style-preserved VC/SVC. P10-S uses a fixed local bridge rather than its demo
  defaults so it cannot silently download models or use example voices.
- Python: upstream recommends Conda with Python 3.10 and
  `models/svc/vevo2/requirements.txt`.
- Model source: <https://huggingface.co/RMSnow/Vevo2>, whose published tree is
  about 11 GB. The AR optimizer alone is 2.04 GB and the total exceeds P10-S3's
  8 GiB model-download budget.
- Model terms: model card reports CC-BY-NC-ND-4.0; this is separate from the
  MIT source license and requires product/legal review.
- CUDA/VRAM: no verified estimate is recorded; full model download is blocked
  by budget before a trustworthy benchmark can be made.
- Windows RTX 4080: **UNKNOWN**. Mac: **UNKNOWN**.

## Demucs separation

- Package: <https://github.com/facebookresearch/demucs> (optional local Python
  API/module runtime, not a public service).
- P10-S adapter: fixed `python -m demucs.separate -n htdemucs --repo
  <trusted-local-repo> --two-stems vocals`; the official `--repo` seam selects
  `LocalRepo`/`BagOnlyRepo` instead of the remote model path. Execution remains
  bounded, uses a private temporary directory, and has no shell.
- Model source/size/VRAM: inspect the installed package's official model
  release before download. No Demucs model has been downloaded in P10-S3.
