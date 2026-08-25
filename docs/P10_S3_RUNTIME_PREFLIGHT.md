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

Before the Git check, the preflight confirms that the configured Python path is
an existing regular file and that the configured working directory exists.
This common readiness gate applies to Seed-VC, RVC, Vevo2, and Demucs; it
blocks process launch with the adapter's existing bounded unavailable result.

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

Those declarations are intentionally bound to the pinned upstream layout, not
an arbitrary cache folder. `rmvpe` and `campplus` use
`<seed-vc>/checkpoints`, because `hf_utils.load_custom_model_from_hf` passes
`cache_dir="./checkpoints"`. `bigvgan` and `whisper` use
`<seed-vc>/checkpoints/hf_cache`, because `inference.py` sets `HF_HUB_CACHE`
to that path before its `from_pretrained` calls. A trusted runner in the
Seed-VC Python environment asks Hugging Face to resolve RMVPE and CAMPPlus with
`hf_hub_download(..., local_files_only=True)`, resolves local BigVGAN and
Whisper snapshots with `snapshot_download(..., local_files_only=True)`, then
resolves Whisper config/feature-extractor artifacts with Transformers'
`local_files_only=True`. It does not infer Hugging Face cache internals from
directory names. A missing or incomplete cache fails before engine launch.

The child receives process-local `HF_HUB_OFFLINE=1`,
`TRANSFORMERS_OFFLINE=1`, and `HF_DATASETS_OFFLINE=1`. It otherwise inherits
the host environment, including PATH, SYSTEMROOT, and CUDA variables. Missing
dependencies produce bounded `engine_unavailable`; they cannot trigger a
silent transfer. No global environment or firewall setting is changed.

## Demucs offline gate

Demucs always invokes the fixed `python -m demucs.separate ... --repo
<trusted-local-repo>` argument. The upstream `get_model` code uses
`LocalRepo(repo)` for component `.th` files and `BagOnlyRepo(repo, model_repo)`
for the selected bag YAML when `--repo` is supplied; it does not take the
remote `files.txt` branch. A trusted runner in the Demucs Python environment
parses the selected bag YAML, constructs Demucs' own `LocalRepo`, and requires
every signature referenced by `models` to resolve through that repository.
No duplicate hand-maintained component list or remote fallback is involved.

Missing runtime/model maps to `separator_unavailable`, process exit or missing
stems maps to `separation_failed`, and timeout/cancellation preserve their
bounded codes.

## Safe Windows readiness command

After cloning the exact source revision, preparing every dependency locally,
and copying the example into ignored `singing_runtime/config.toml`, run:

```powershell
Set-Location 'C:\path\to\Yogurt_Chat_Bot'
& 'E:\Yogurt_Singing_Runtime\envs\seedvc\Scripts\python.exe' -m capabilities.singing.bakeoff_cli `
  --sample-id zh-proto --engine seed_vc --language zh --transpose 0 --preflight-only
```

It prints only bounded JSON: `READY`, verified revision, and the configured
non-sensitive model/profile identifiers, or a bounded error. It never prints
private paths, config content, raw stderr, or a Python traceback. Only `READY`
authorizes the separate short first-proof command.
