# P10-S3 — Real SVC Bake-off

## Test architecture

P10-S3 remains a standalone Singing Capability. The public `CoverRequest`
does not accept an engine, path, URL, executable, checkpoint, or command. A
private, ignored `singing_runtime/config.toml` composes an approved local
runtime. `python -m capabilities.singing.bakeoff_cli` only accepts an opaque
sample ID, engine name, language, and bounded transpose.

The adapters use `asyncio.create_subprocess_exec`, fixed argv, fixed working
directory, timeout, private temporary inputs/outputs, suppressed raw backend
stdio, and strict PCM WAV validation. Failed process output and private paths
never reach `SingingResult`, events, or the portable `BakeoffRunManifest`.

## Asset protocol and metrics

Use only authorized material. Start with one 15–30 second vocal sample,
`output=vocal`, 44.1 kHz PCM WAV, and transpose `0`. Later use independent
**zh**, **ja**, and **en** samples covering normal register, sustained/vibrato,
and fast transitions. Keep source song, stems, references, models, transcript,
and output WAVs outside Git.

For every run record engine/revision, opaque sample ID/language, source/output
duration, sample rate, wall time, RTF, transpose, success/error, RAM/VRAM when
available, F0 correlation, F0 RMSE cents, voiced/unvoiced agreement, and
duration ratio. The latter four describe melody fidelity only; they do not
prove naturalness or voice identity. Use the fixed 1–5 subjective rubric in
[P10_S_BAKEOFF_PROTOCOL.md](P10_S_BAKEOFF_PROTOCOL.md).

## Current real-runtime status

No authorized source vocal, authorized Yogurt reference, private runtime,
ffmpeg, or NVIDIA GPU is available on this Mac. No engine/model/audio run was
performed, so no latency, memory, quality, or identity value is invented.

- Separation proof: `NOT_RUN`.
- Seed-VC zero-shot proof: `NOT_RUN`.
- RVC model: `NOT_TRAINED`.
- Vevo2 real POC: `DEFERRED_MODEL_SIZE` (official published model tree is about
  11 GB, above the P10-S3 8 GiB total download limit).

The current `VocalArtifact` retains WAV bytes in memory. PCM 16-bit stereo at
44.1 kHz is about 5.05 MiB for 30 seconds, 10.09 MiB for 60 seconds, and
40.37 MiB for four minutes per artifact. P10-S3 bounds real adapter output at
16 MiB / 70 seconds; full-song work needs benchmark evidence before introducing
an internal temporary-artifact seam. The public API remains path-free.

## Windows RTX 4080 first-proof procedure

Create isolated runtime roots; do not install these dependencies into P10 core
requirements:

```powershell
$root = 'E:\Yogurt_Singing_Runtime'
New-Item -ItemType Directory -Force -Path "$root\envs", "$root\upstream", "$root\models", "$root\private", "$root\outputs"
git clone https://github.com/Plachtaa/seed-vc.git "$root\upstream\seed-vc"
Set-Location "$root\upstream\seed-vc"
git checkout 51383efd921027683c89e5348211d93ff12ac2a8
py -3.10 -m venv "$root\envs\seedvc"
& "$root\envs\seedvc\Scripts\Activate.ps1"
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Before any model transfer, run the official-host preflight and stop if all
selected/transitive downloads exceed 8 GiB or a single file exceeds 4 GiB:

```powershell
hf download Plachta/Seed-VC DiT_seed_v2_uvit_whisper_base_f0_44k_bigvgan_pruned_ft_ema.pth --dry-run
```

After the user has placed only authorized 15–30 second WAVs in the private
runtime and the size preflight is within budget, copy the committed example to
the ignored local config, replace placeholders, then run:

```powershell
Set-Location 'C:\path\to\Yogurt_Chat_Bot'
Copy-Item singing_runtime\config.example.toml singing_runtime\config.toml
& 'E:\Yogurt_Singing_Runtime\envs\seedvc\Scripts\python.exe' -m capabilities.singing.bakeoff_cli --sample-id zh-proto --engine seed_vc --language zh --transpose 0
```

Listen to the resulting private WAV before assigning quality. A technical WAV
without Yogurt-like identity is `FUNCTIONAL_PASS_QUALITY_HOLD`, not quality
pass. No full song, parameter sweep, or training should start before this
short proof succeeds.

## RVC and Vevo2 gates

RVC requires a private dataset manifest audit and a locally trained Yogurt
checkpoint before inference is meaningful. For the current upstream pin, set
up a separate Python 3.12/CUDA 11.8 environment only after official asset-size
inspection. Do not begin multi-epoch training until the preflight records
minutes, clips, pitch range, estimated epochs/time, VRAM, and disk. More than
30 minutes of planned GPU work requires explicit approval.

Vevo2's official model tree exceeds the download budget. Its adapter and
fixed no-auto-download bridge are present for future use, but model download
and real POC are deferred.
