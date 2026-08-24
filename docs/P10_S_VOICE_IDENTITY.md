# P10-S — Speech/Singing Voice Identity

Speech Yogurt (GPT-SoVITS) and Singing Yogurt (future SVC/SVS profile) are
separate models. Shared branding or a shared short speech reference does not
mathematically guarantee identical identity.

## Practical continuity strategy

- Use only authorized identity material with consistent microphone, language,
  and intended persona characteristics.
- Prefer broad-range authorised singing material for a serious profile: sustained
  notes, transitions, vibrato, soft/loud dynamics, and comfortable high/low
  register. Speech references can support an early zero-shot POC only.
- Compare speaker similarity by register, not just on a central speaking pitch.
- Keep source content/melody separate from source identity: SVC should preserve
  lyrics, timing, F0 and expression while minimizing source-singer timbre leak.
- Test formants and high-register timbre explicitly. A transpose can protect
  range but may introduce formant or naturalness drift.

## Profile boundary

`singing_profile="yogurt"` names a trusted application-selected SVC profile;
it is not the existing GPT-SoVITS speech checkpoint and it exposes neither
reference paths nor checkpoint paths. Training references, profile metadata,
and checkpoints remain private local assets.

## Evaluation limits

Use the bake-off scorecard plus a small reference listening panel. Describe
results as observed similarity under tested conditions—not as a claim of
identity equivalence or impersonation capability. Training requires authorization
for every voice asset and may require per-model licensing review.
