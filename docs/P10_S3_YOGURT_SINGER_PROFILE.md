# P10-S3 — Yogurt Singer Profile

`YogurtSingerProfile` is a trusted application-owned record, not a public
request object. The public surface remains `singing_profile="yogurt"`; callers
cannot choose a checkpoint, runtime, device, or reference path.

| Field | Meaning |
|---|---|
| `profile_id` | Stable internal identity, e.g. `yogurt-singer-v1` |
| `engine` | `seed_vc`, `rvc`, or `vevo2` |
| `engine_revision` | Exact upstream revision |
| `checkpoint_id` | Opaque approved checkpoint label, never a path |
| `native_sample_rate` | Engine/output sample rate |
| `recommended_pitch_range` | Audited low/high range, not a guess |
| `supported_languages` | Evidence-backed language set |
| `training_data_summary` | Authorized minutes/clips/types only, no asset list |
| `quality_status` | `not_run`, `functional_hold`, `approved`, or `rejected` |

Speech and singing remain distinct:

```text
Yogurt Voice Identity
├── Speech Profile  -> GPT-SoVITS
└── Singing Profile -> SVC engine/checkpoint
```

Evaluate identity continuity with authorized references: speech-reference
similarity, singing timbre, high-register drift, breathiness/formant drift,
and source-singer leakage. No numerical score replaces human listening.

RVC data manifests remain private. The committed
[schema/example](P10_S3_RVC_DATASET_MANIFEST.example.json) contains only opaque
`asset_id`, duration, sample rate, language, type, and non-sensitive quality
notes. A final Singer training gate normally seeks 10–30 minutes of clean
authorized singing across range; below 5 minutes or a speech-dominant set is
`DATASET_INSUFFICIENT_FOR_FINAL_SINGER`, though a short approved POC may be
possible.
