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

## Deliberately deferred seams

Synthesis is separate from playback. A future `PlaybackController` can choose
Mac speakers, Live2D, a robot speaker, or a streaming client without changing
the Mouth synthesis contract.

Future B2 work may add a `MouthEvent` stream such as `speech_started`,
`audio_chunk`, and `speech_finished`, plus verified PCM metadata for lip-sync.
This phase does not claim streaming, cancellation, phoneme, amplitude, or
viseme support.
