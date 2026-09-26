# P10-S — SVC Bake-off Protocol

## Scope and assets

Compare one trusted, authorized source vocal and the same authorized Yogurt
identity material across engines. Keep assets only in the local Singing Asset
Registry; do not commit songs, separated stems, references, checkpoints,
transcripts, or rendered covers.

Use 15–30 second clips for **zh**, **ja**, and **en**, each including normal
register, a sustained note, vibrato, pitch transition, and fast syllables.
Run the same separator output, Yogurt profile, transpose, and output sample
rate for every candidate.

## Candidate matrix

Run Seed-VC, RVC, and Vevo2 when their approved local environments and models
are present. For each result, the `SingingBakeoffHarness` manifest records only:

- engine and engine version;
- opaque sample ID and language;
- transpose;
- wall time, output duration, sample rate;
- success/failure and bounded error code.

Optional private local measurements: peak RAM, peak VRAM, CPU/GPU utilization.
Never include local paths, lyrics, raw reference descriptions, model location,
or raw stderr in the manifest.

## Subjective scorecard

Rate each dimension 1–5 (1 unusable, 3 acceptable for POC, 5 release quality):

| Dimension | What to listen for |
|---|---|
| Yogurt timbre similarity | stable identity, especially across register |
| Pitch fidelity | melody/F0 follows source and selected transpose |
| Timing fidelity | syllables and phrasing remain aligned |
| Lyric intelligibility | words remain understandable in each language |
| High-note stability | no severe collapse, metallicity, or octave errors |
| Vibrato preservation | periodic motion is retained without exaggerated wobble |
| Expression preservation | dynamics and phrasing survive without importing source identity |
| Source-singer leakage | lower leakage score means better conversion; record separately |
| Artifacts/noise | clicks, distortion, bleed, robotic texture |
| Overall naturalness | holistic listening judgment |

Record two blinded listeners where available, clip order randomized, and keep
the rubric constant. Do not declare an engine winner from a single song.

## Gate to next phase

Advance to Yogurt Singer training only after authorized assets demonstrate a
repeatable candidate advantage in all three languages or an explicitly narrower
supported-language claim. Separation bleed, high-register drift, and source
identity leakage are release blockers, not cosmetic issues.
