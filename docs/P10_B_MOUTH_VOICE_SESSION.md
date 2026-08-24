# P10-B — Yogurt Mouth and VoiceSession

## Status

- P10-B0 Voice Orchestrator: **PASS**.
- P10-B1 Mac runtime functional path: **PASS**.
- P10-B1-5 resource acceptance: **HOLD** because the 16 GB Mac has material
  swap and memory-compression pressure when Brain and TTS coexist.
- P10-B1-6 WeChat auto-TTS: **DEFERRED**. WeChat is a text channel; it does
  not own Yogurt's speech boundary.
- P10-B2 Mouth: **ACTIVE** on the P10-B2/B3 branch.
- P10-B3 VoiceSession: **ACTIVE** on the P10-B2/B3 branch.

## Architecture

```text
Yogurt Core
  ↓
VoiceSession
  ↓
Mouth
  ↓
Voice Orchestrator
  ↓
Voice Runtime (127.0.0.1:8770)
  ↓
GPT-SoVITS (127.0.0.1:9880)

             Audio
               │
      ┌────────┼────────┐
      ↓        ↓        ↓
   Local     Future    Future
   Speaker   Live2D    Robot
```

Mouth owns bounded final-text synthesis. VoiceSession owns when speech happens,
its bounded queue, playback-level interruption, and trusted local sink routing.
Neither component knows WeChat, OpenClaw, Memory, Sticker, ProjectState, or an
LLM implementation.

## B2 contract

`SpeakRequest` contains only bounded `text`, `scene`, and `language`. Scene
policy remains in Voice Orchestrator. Callers cannot select an endpoint, model,
checkpoint, reference audio, speed, or local path.

`SpeakResult` carries only in-memory audio, safe metadata, timing, and a
bounded public error code. It never relays a GPT-SoVITS body or local private
asset detail.

Playback is separate from synthesis. The macOS sink invokes only fixed
`/usr/bin/afplay` using an argv list and a private temporary WAV. Cancellation
is real for pending/playing audio. A3 synthesis itself is non-streaming and
non-cancellable; a cancelled session discards a later synthesis result.

Real synthesis streaming is **DEFERRED**. `MouthEvent.audio_chunk` is only a
future contract and is not emitted by the current complete-WAV runtime.

Mouth emits one bounded lifecycle per utterance: `queued`, `synthesis_started`,
`audio_ready`, `playback_started`, then `speech_finished`; cancellation and
failure end with `speech_cancelled` or `speech_failed`. `playback_started` is
emitted only once a trusted sink has actually begun playback. `audio_chunk` is
future-only.

Lip sync is amplitude-only, based on deterministic windowed WAV/PCM RMS frames.
It does not claim phoneme or viseme recognition and never expands complete PCM
audio into a Python float list.

## B3 contract

VoiceSession uses opaque UUID session and utterance IDs, one active playback per
session, a FIFO order within equal priority, three priorities (`LOW`, `NORMAL`,
`HIGH`), and a deterministic max queue of 1–32 entries. Excess submissions are
rejected with `queue_full`.

Sinks are registered in-process by a trusted application. A caller may select
only a known sink ID; no URL, URI, LAN discovery, command, or device path is
accepted. Snapshots and bounded lifecycle events omit text, audio, prompts,
reference paths, and backend detail.

Synthesis is serialized across VoiceSessions sharing an asyncio loop, matching
the current Voice Runtime single-request capacity. Playback is instead
serialized per trusted sink in `SinkRegistry`: sessions using one physical sink
cannot overlap, while different registered sinks may play independently.

## Explicit non-goals

- WeChat speech replies or automatic OpenClaw TTS;
- Singing (reserved for separate P10-S);
- STT, Live2D rendering, robot control, cloud/browser speech access;
- remote/LAN runtime access;
- GPT-SoVITS streaming, phoneme, or viseme inference.
