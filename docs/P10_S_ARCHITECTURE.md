# P10-S — Singing Capability Architecture

## Boundary

P10-S is independent from Mouth, VoiceSession, WeChat, OpenClaw, and the
speech-only Voice Runtime. Cover jobs must use an authorised source song that
an application has already registered under an opaque asset ID.

```text
Yogurt Core
  -> SingingService
     -> SingingAssetRegistry (asset_id -> private local asset)
     -> SourceSeparator
     -> SingingConversionEngine
     -> RemixService
     -> in-memory cover result
```

`CoverRequest` uses Pydantic `extra="forbid"`; it has no path, URL, command,
module, model, checkpoint, executable, or ffmpeg-argument field. Results and
events expose opaque IDs, bounded metadata, and bounded error codes only.

## Cover flow and lifecycle

`queued -> asset_ready -> separation_started -> separation_finished ->
conversion_started -> conversion_finished -> remix_started -> cover_ready`.
Failures terminate once with `failed`; cancellation terminates once with
`cancelled`. Paths, lyrics, model identifiers, stderr, and tracebacks are never
events.

The foundation treats engines as potentially non-cancellable Python inference:
the service marks cancellation, stops starting later stages, and discards a
later result. A future subprocess-backed adapter may implement real termination
behind the same protocol, but must use a fixed trusted executable, argv lists,
bounded timeout/output, a controlled temporary directory, and cleanup.

## Engine and separation composition

`SingingConversionEngine` receives an in-memory vocal artifact, a fixed
`yogurt` singing profile, and bounded conversion options. The application, not
the caller, selects RVC, Seed-VC, or Vevo2. `SourceSeparator` similarly hides
Demucs or a future benchmarked separator. `RemixService` owns only bounded
vocal/instrumental gain and peak-normalisation choices; advanced mastering is
deferred.

## Transpose and Native Singing

Schema supports `-12..+12` semitones. A production cover may only apply a
transpose when its chosen pipeline can transpose both vocal and instrumental
consistently; synchronized instrumental transposition is therefore deferred in
this foundation. Native Singing is a separate future chain:

```text
lyrics + MIDI/melody -> DiffSinger / Vevo2-like SVS -> Yogurt Singing
```

## Deployment and roadmap

No P10-S service binds 8770 or 9880. Mac is suitable for orchestration and
benchmarking only after evidence; Windows RTX 4080 is the preferred adaptation
and bake-off host. Future deployment candidates are Mac local inference,
Windows local inference behind a bounded local bridge, or offline Windows song
jobs—never an inbound cloud/LAN command bus.

- S0 Architecture / Boundary: **PASS**
- S1 Source Separation abstraction: **PASS** (Demucs seam; runtime deferred)
- S2 SVC Engine Bake-off foundation: **PASS**
- S3 Yogurt Singer Profile: deferred
- S4 Full Cover Pipeline: deferred
- S5 Typed Singing Runtime: deferred
- S6 Singing Orchestrator: deferred
- S7 Native Singing Synthesis: deferred
