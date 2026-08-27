# P11-A1 Native Local Voice Channel

P11-A1 adds a local, half-duplex Push-to-Talk interaction surface. It is not a
WeChat voice-reply feature and it never sends a WeChat message.

```text
Microphone
  -> local whisper.cpp STT
  -> OpenClaw/Yogurt agent (main, current Qwen)
  -> final assistant text
  -> VoiceSession
  -> Mouth
  -> Voice Orchestrator / local TTS runtime
  -> macOS speaker
```

## Boundaries

- `native_voice` has no WeChat adapter import, account knowledge, channel
  argument, or delivery command.
- The Brain client invokes the existing OpenClaw agent with a distinct
  `native:voice:<opaque-session-id>` session key, `--json`, and no `--deliver`.
  It therefore uses the configured Yogurt Brain (currently `main` with
  `ollama/qwen3.5:9b`) rather than copying a SOUL prompt or creating another
  Ollama loop.
- Native Memory is hard-disabled (`memory.mode = "off"`) in A1. No Native turn
  calls the production Memory Service, so current V5-B.7 shadow events remain
  WeChat-only. The existing OpenClaw social plugin additionally bypasses V2/V3/
  V4 hooks for `native:voice:*` session keys, so a local Brain request cannot
  be mistaken for a WeChat turn by globally enabled hooks.
- Final text is submitted to the existing `VoiceSession`, which serializes the
  queue and playback, and then to `MouthService` / Voice Orchestrator. Native
  Voice does not talk to a TTS model, checkpoint, or HTTP endpoint directly.
- STT language is resolved from the transcript, while TTS language is resolved
  independently from final assistant text (`zh`, `ja`, or `en`). A reply is not
  assumed to match the user's input language.

## Setup and use

Copy `native_voice/config.example.toml` to `native_voice/config.toml` and set
absolute, trusted local paths for a preinstalled whisper.cpp executable and
model. The real config is Git-ignored. P11-A1 never downloads whisper.cpp or a
model.

For an actual Mac microphone runtime, install the explicit Native Voice runtime
dependencies first. This only installs the Python backend; it does not download
or access microphone hardware during installation or CI.

```bash
python3 -m pip install -r native_voice/requirements-runtime.txt
```

```bash
python -m native_voice.cli
```

The CLI prints `READY`; press Enter to begin capture and Enter again to stop.
Capture is mono 16 kHz PCM16 WAV within a private `TemporaryDirectory`, which
is deleted for every terminal state. No transcript, reply, WAV, prompt, or
model path is persisted by Native Voice. CLI display is intentionally limited
to `YOU: <text>` and `YOGURT: <final text>` for the active terminal session.

Cancellation stops capture, terminates/reaps local STT and Brain subprocesses,
or cancels the existing `VoiceSession` playback handle. A late result cannot
start speech after cancellation. While Yogurt is thinking or speaking, a new
microphone turn is rejected, preventing TTS from becoming STT input.

## Phases

`idle -> listening -> transcribing -> thinking -> speaking -> finished`

Terminal phases are `cancelled` and `failed`. Session and turn identifiers are
opaque UUIDs. Events contain only IDs, phase, duration, and a bounded error
code—never audio, transcript, local paths, prompts, references, checkpoints, or
credentials.

## Safety and next phases

P11-A1 is manual half-duplex PTT only: no always-listening, wake word, VAD,
barge-in, AEC, or background ambient capture. The whisper.cpp command is fixed
argv with `shell=False`, bounded timeout/stdout, hidden stderr, and cancellation
reaping; speech never controls executable/model arguments.

- P11-A2: Daily expressive-TTS style bridge, only if it fits the current Mouth
  architecture without a second orchestration stack.
- P11-A3: VAD, wake word, barge-in, and AEC.
- P11-B: Eyes.
- P11-C: Live2D body.

Native Memory integration is deferred until V5-B.7 shadow evaluation completes.
