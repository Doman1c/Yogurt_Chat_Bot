# P10-S — Open-source SVC and Separation Research

Checked 2026-08-25. This is an engineering comparison, not a legal conclusion.
Each source repository and each downloadable model/checkpoint must be reviewed
separately before adoption.

| Project | Type | License | Maintenance | Zero-shot | Fine-tune | F0 / transpose | Singing fidelity | Mac feasibility | Windows CUDA | Integration complexity | P10-S role |
|---|---|---|---|---|---|---|---|---|---|---|---|
| RVC WebUI | VC/SVC | MIT + project agreement | active repo | no primary zero-shot path | yes; project says <=10 min data can work | pitch models incl. RMVPE; semitone control | strong established baseline, retrieval can help timbre | possible but not first target | strong | medium; isolate its UI/CLI | bake-off candidate |
| Seed-VC | VC/SVC | GPL-3.0 | active README/changelog | yes, 1–30 s reference | yes | 44.1k SVC model, F0 condition, semitone shift | purpose-built 44.1k SVC/BigVGAN path | official M-series requirements | recommended for real time | high; GPL and large checkpoint runtime | bake-off candidate |
| Amphion Vevo2 | unified speech/singing TTS, SVS, VC/SVC | Apache-2.0 | actively developed | yes | research-oriented training options | prosody/melody controls | broadest control surface; needs proof | unknown until benchmark | preferred research host | high | advanced candidate / Native roadmap |
| so-vits-svc fork | SVC | MIT | current releases, upstream family fragmented | no | yes | ContentVec, CREPE F0, VITS/NSF-style lineage | historical SVC baseline | possible, not priority | CUDA-oriented docs | medium/high | benchmark reference only |
| DiffSinger | SVS | Apache-2.0 | maintained ecosystem | no | yes | lyrics + MIDI, pitch/variance labels | Native Singing rather than cover conversion | not target | training target | high | Native future |
| SOME | singing-to-MIDI | MIT | successor GAME noted | n/a | custom training possible | extracts non-integer MIDI | analysis/labeling, not SVC | possible | strong | medium | MIDI extraction future |
| Demucs | separation | MIT | archived/read-only | n/a | n/a | n/a | strong vocal/accompaniment baseline, bleeding remains | documented macOS support | CUDA supported | medium, heavy optional dependency | first separator seam |

## Upstream provenance and technical findings

### RVC

- Repository: <https://github.com/RVC-Project/Retrieval-based-Voice-Conversion-WebUI>
- Checked revision: `81eed5e8f68b6bed1789f682fe78cdd324495afc` (2026-08-25).
- License: MIT; repository UI also carries an output-use responsibility notice.
- Architecture: VITS-based voice conversion with content features, optional F0,
  RMVPE/other pitch extractors, HiFi-GAN-family components, and optional FAISS
  retrieval index. Its training notes explicitly say pitch-aware models are
  needed for singing and describe FAISS index training.
- Suitability: mature fine-tuned SVC baseline and obvious Windows CUDA bake-off
  candidate. Retrieval may reduce timbre drift but can also expose/source-leak
  artifacts; measure leakage rather than assume it disappears.
- Risk: project WebUI/CLI exposes many local parameters, so P10-S must compose
  it behind a fixed adapter. Mac compatibility is not an acceptance claim.

### Seed-VC

- Repository: <https://github.com/Plachtaa/seed-vc>
- Checked revision: `51383efd921027683c89e5348211d93ff12ac2a8` (2026-08-25).
- License: GPL-3.0 for source; Hugging Face model terms remain separate.
- Architecture: released 44.1 kHz SVC preset uses Whisper content features,
  F0 conditioning and BigVGAN; its documented flags include F0 conditioning
  and semitone shift. README documents zero-shot SVC from a 1–30 second
  reference and custom fine-tuning.
- Suitability: first zero-shot SVC candidate, including the required source
  melody/timing preservation evaluation. Official README has an Apple Silicon
  requirements file, but real long-song speed/memory remains benchmark-only.
- Risk: automatic checkpoint download must remain disabled from P10-S code;
  GPL implications require separate distribution review.

### Amphion / Vevo2

- Repository: <https://github.com/open-mmlab/Amphion>
- Checked revision: `26f6883110181f1dbfe95c70a7c7dbaf4de5f42a` (2026-08-25).
- License: Apache-2.0 source; pretrained-data/model terms must be checked per
  release.
- Architecture: Vevo2 documents unified controllable speech and singing
  generation, including TTS, text-to-singing, VC/SVC, editing, singing style
  conversion, humming/instrument-to-singing, and prosody-token conditioning.
- Suitability: highest-upside candidate for SVC plus future Native Singing
  seams, not a first production runtime because setup/model footprint and
  operating envelope are materially more complex than Seed-VC/RVC.

### so-vits-svc

- Repository evaluated: <https://github.com/voicepaw/so-vits-svc-fork>
- Checked revision: `f5624868d3faa5c1f09b83ca7b894ce2fe8a5f9b` (2026-08-25),
  latest release observed `v4.2.30`.
- License: MIT source; dependencies and public models are separate.
- Architecture: SoftVC/ContentVec content representation, CREPE pitch option,
  VITS-family generator and NSF-HiFiGAN lineage. The fork documents data
  preparation, short segments, CUDA requirements and real-time features.
- Suitability/risk: useful historical fine-tuned baseline, but not the default
  shortlist winner; original/fork maintenance and checkpoint compatibility vary.

### DiffSinger and SOME

- DiffSinger: <https://github.com/openvpi/DiffSinger>, revision
  `1b0a6da195646cdc4f8051d7afcc40e16960446c`, Apache-2.0. It is the Native
  Singing path: authorized lyrics, phonemes, timing and MIDI/melody become
  controlled synthesis rather than source-vocal conversion.
- SOME: <https://github.com/openvpi/SOME>, revision
  `e523eef70777c1370199363de03eb37d66dce76e`, MIT. It extracts singing MIDI,
  supports non-integer pitches useful for DiffSinger labels, and explicitly
  prohibits training on recordings obtained without consent. It is not needed
  for current Cover Mode.

### Demucs and separator benchmark note

- Repository: <https://github.com/facebookresearch/demucs>, revision
  `e976d93ecc3865e5757426930257e200846a520a` (2026-08-25), MIT.
- Architecture: Hybrid Transformer Demucs v4 combines waveform/spectrogram
  paths and separates vocals from accompaniment (plus other stems).
- Maintenance: archived/read-only and explicitly not actively maintained;
  useful as an established baseline, not a permanent unreviewed dependency.
- Alternative: Ultimate Vocal Remover/MSST/MDX-family approaches are benchmark
  candidates only. They are not implemented now because model licenses and
  reproducibility need separate validation.

## Recommended P10-S bake-off roles

1. **Cover SVC shortlist:** Seed-VC 44.1k F0 model, fine-tuned RVC, and Vevo2.
2. **Separation:** Demucs as initial baseline; compare one MDX/UVR-family
   separator only if vocal bleed limits conversion scores.
3. **Native Singing future:** DiffSinger first; Vevo2 is a research alternative.
4. **MIDI extraction future:** SOME, with manual review of extracted notes.

Sources: official repositories and their README/LICENSE files:
[RVC](https://github.com/RVC-Project/Retrieval-based-Voice-Conversion-WebUI),
[Seed-VC](https://github.com/Plachtaa/seed-vc),
[Amphion Vevo2](https://github.com/open-mmlab/Amphion/tree/main/models/svc/vevo2),
[so-vits-svc fork](https://github.com/voicepaw/so-vits-svc-fork),
[DiffSinger](https://github.com/openvpi/DiffSinger),
[SOME](https://github.com/openvpi/SOME), and
[Demucs](https://github.com/facebookresearch/demucs).
