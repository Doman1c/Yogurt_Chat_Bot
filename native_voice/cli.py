"""Manual Push-to-Talk CLI for the local-only Native Voice channel."""

from __future__ import annotations

import argparse
import asyncio

from capabilities.mouth import MouthService
from capabilities.mouth.playback import MacOSPlaybackSink
from capabilities.voice_session import OutputRouter, SinkRegistry, VoiceSession

from .audio import AudioCaptureError, SoundDeviceMicrophone
from .brain import OpenClawAgentBrain
from .config import ConfigError, load_config
from .service import NativeVoiceBusyError, NativeVoiceService
from .stt import WhisperCppSTT


async def _input(prompt: str) -> str:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, input, prompt)


def _event_printer(event) -> None:
    labels = {
        "transcribing": "TRANSCRIBING", "thinking": "THINKING", "speaking": "SPEAKING",
    }
    label = labels.get(event.phase.value)
    if label:
        print(label, flush=True)


async def run(config_path: str | None = None) -> int:
    try:
        config = load_config(config_path)
    except ConfigError as error:
        print("Native Voice config error: %s" % error)
        return 2
    microphone = SoundDeviceMicrophone()
    voice_session = VoiceSession(
        MouthService(), OutputRouter(SinkRegistry({"macos": MacOSPlaybackSink()}), "macos")
    )
    service = NativeVoiceService(
        microphone, WhisperCppSTT(config.stt), OpenClawAgentBrain(config.brain), voice_session,
        event_handler=_event_printer,
    )
    print("YOGURT NATIVE VOICE")
    print("READY")
    while True:
        command = (await _input("Press Enter to talk, or q to quit: ")).strip().lower()
        if command in {"q", "quit", "exit"}:
            return 0
        try:
            await service.start_listening()
            print("LISTENING")
            stop = (await _input("Press Enter to stop, or c to cancel: ")).strip().lower()
            if stop == "c":
                await service.cancel()
                print("CANCELLED")
            else:
                result = await service.stop_listening_and_process()
                if result.transcript is not None:
                    print("YOU: %s" % result.transcript.text)
                if result.reply is not None:
                    print("YOGURT: %s" % result.reply.text)
                if result.status.value == "failed":
                    print("FAILED: %s" % (result.error_code or "native_voice_failed"))
        except (AudioCaptureError, NativeVoiceBusyError) as error:
            print("FAILED: %s" % error)
        print("READY")


def main() -> None:
    parser = argparse.ArgumentParser(description="Yogurt local Push-to-Talk voice channel")
    parser.add_argument("--config", default=None, help="trusted local Native Voice TOML config")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(run(args.config)))


if __name__ == "__main__":
    main()
