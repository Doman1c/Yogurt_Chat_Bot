# Yogurt Mouth

Yogurt Mouth is Yogurt's standalone speech capability. It accepts already
determined text and returns in-memory audio through a small, typed interface.

It is not a WeChat TTS plugin, a GPT-SoVITS API exposed to a UI, or a model
training layer. It has no OpenClaw hooks, WeChat session dependency, Sticker,
ProjectState, memory, or second LLM call.

```text
Caller
  ↓
Yogurt Mouth
  ↓
Voice Orchestrator
  ↓
Voice Runtime (127.0.0.1:8770)
  ↓
GPT-SoVITS
```

Potential callers are Yogurt Native Chat, a Live2D desktop pet, a desktop app,
and a robot embodiment. They only know `SpeakRequest` and `SpeakResult`; the
runtime endpoint, model, reference audio, profile paths, speed, and backend
details remain internal.

## Public API

```python
from capabilities.mouth import MouthService, SpeakRequest

result = await MouthService().speak(
    SpeakRequest(text="主人，欢迎回来。", scene="casual", language="zh")
)
```

`scene` is limited to `casual`, `technical`, `playful`, `comfort`,
`celebration`, and `serious`. Existing Voice Orchestrator policy maps the
scene to its bounded voice plan; Mouth never accepts emotion, intensity, speed,
reference paths, model paths, or endpoint URLs from callers.

## Boundary and safety

- The default client is the existing loopback-only Voice Orchestrator client.
- Text is bounded to 500 characters and blank input is rejected.
- Public failures are bounded codes only; upstream response bodies and local
  paths are not returned.
- Audio remains in memory. Mouth neither persists it nor starts playback.
- Browser and cloud callers do not receive direct access to ports 8770 or 9880.

## Playback and interruption

`MouthService` performs synthesis only. `PlaybackController` accepts an
in-memory WAV `AudioArtifact` and a trusted `PlaybackSink` separately. The
included `MemoryPlaybackSink` is for tests/buffer consumers; `MacOSPlaybackSink`
uses only `/usr/bin/afplay`, writes a private temporary WAV, and removes it when
playback finishes or is cancelled. No caller supplies an executable or path.

Playback cancellation is real when audio is pending or playing. It does not
cancel the P10-A3 synthesis request: GPT-SoVITS generation is non-streaming and
non-cancellable, so a later VoiceSession discards its completed artifact rather
than claiming generation stopped.

## Future streaming and lipsync seams

Synthesis is separate from playback. A future `PlaybackController` can choose
Mac speakers, Live2D, a robot speaker, or a streaming client without changing
the Mouth synthesis contract.

`MouthEvent` emits `synthesis_started` only after VoiceSession acquires the
shared synthesis slot, and `playback_started` only after a trusted sink has
actually started playback. `audio_chunk` remains reserved for a future
streaming runtime and is not emitted by the current full-audio synthesis path.

`amplitude_envelope()` provides deterministic, standard-library WAV/PCM RMS
frames for future mouth-open animation. Amplitude lipsync is not phoneme or
viseme lipsync; this phase deliberately does not infer A/I/U/E/O mouth shapes.

## VoiceSession

`capabilities.voice_session.VoiceSession` sits above Mouth when a caller needs
queueing, priority, trusted sink routing, playback cancellation, and safe
observability. Its `session_id` and opaque utterance IDs are Yogurt voice IDs;
they are never WeChat or OpenClaw session/message identifiers.

Playback arbitration is per trusted sink inside `SinkRegistry`. All
VoiceSessions in one process that target the same physical output must share the
same application-level `SinkRegistry` / `OutputRouter` composition. Two
separate registries representing the same physical speaker own independent
locks and therefore do not provide cross-session serialization. This sharing
rule is an application-composition contract rather than a global singleton.
