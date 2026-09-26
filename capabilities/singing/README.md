# Yogurt Singing

Yogurt Singing is a standalone capability for authorised-song **Cover Mode**.
It is not a Mouth scene, a WeChat feature, an OpenClaw hook, or an extension of
the speech runtime on ports 8770/9880.

```text
registered song asset
  -> trusted separator -> source vocal + instrumental
  -> trusted SVC engine -> Yogurt vocal
  -> trusted remix service -> in-memory Yogurt cover
```

The public API accepts an opaque `asset_id`, the fixed `yogurt` singing profile,
bounded transpose (`-12..+12`), and bounded expression/remix options. It never
accepts a file path, model/checkpoint path, executable, command, backend URL,
or raw ffmpeg flags. `SingingAssetRegistry` is application composition: it maps
the opaque ID to a local path without returning that path in results or events.

The current implementation is a typed foundation with fake-testable separator,
SVC-engine, and remix seams. `RVCAdapter`, `SeedVCAdapter`, `Vevo2Adapter`, and
`DemucsSeparator` intentionally report a bounded unavailable error until an
application installs and explicitly composes an approved local runtime.
They do not download models or start a web service.

`SingingService.submit()` returns an opaque handle. A cancellation **request**
is immediate: a queued job waiting for capacity completes immediately and never
starts the pipeline. A running non-interruptible separator, engine, or mixer
may finish its current call; the service then skips later stages, discards late
output, and returns `cancelled`. No adapter claims GPU cancellation unless it
implements it.

Only one cover pipeline runs per service composition by default. The default
pending bound is four jobs; further submissions receive `queue_full`. A mixed
cover with nonzero transpose is rejected as `unsupported_transpose` unless the
trusted remix composition explicitly declares synchronized instrumental
transpose support. Vocal-only transpose remains valid.

Current focus is SVC Cover Mode. Native Singing (`lyrics + MIDI/melody -> SVS`)
is a future seam, not an implementation in P10-S foundation.
