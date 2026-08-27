from __future__ import annotations

import asyncio
import builtins
import json
import sys
import wave
from pathlib import Path

import pytest

from capabilities.voice_session.schemas import UtteranceState
from native_voice.audio import AudioCaptureError, RecordedAudio, SoundDeviceMicrophone
from native_voice.brain import BrainError, BrainRequest, OpenClawAgentBrain, _final_text
from native_voice.config import BrainConfig, NativeVoiceConfig, STTConfig, _toml_loader
from native_voice.memory import DisabledNativeMemory
from native_voice.schemas import (
    BrainReply,
    TranscriptResult,
    TurnPhase,
    TurnStatus,
    resolve_text_language,
    resolve_transcript_language,
)
from native_voice.service import NativeVoiceBusyError, NativeVoiceService
from native_voice.stt import STTError, WhisperCppSTT


class FakeMicrophone:
    def __init__(self) -> None:
        self.last_path = None
        self.cancelled = False

    def start(self, directory: Path):
        return directory

    def stop(self, capture: Path) -> RecordedAudio:
        path = capture / "input.wav"
        with wave.open(str(path), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(16000)
            output.writeframes(b"\x00\x00" * 16)
        self.last_path = path
        return RecordedAudio(path)

    def cancel(self, capture: Path) -> None:
        del capture
        self.cancelled = True


class FakeSTT:
    def __init__(self, text: str = "酸奶，你听得到我说话吗？", language: str = "zh") -> None:
        self.result = TranscriptResult(text, language)
        self.paths = []

    async def transcribe(self, audio_path: Path) -> TranscriptResult:
        self.paths.append(audio_path)
        return self.result


class BlockingSTT(FakeSTT):
    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()

    async def transcribe(self, audio_path: Path) -> TranscriptResult:
        del audio_path
        self.started.set()
        await asyncio.Event().wait()
        return self.result


class FailingSTT:
    async def transcribe(self, audio_path: Path) -> TranscriptResult:
        del audio_path
        raise STTError("empty_transcript")


class FakeBrain:
    def __init__(self, text: str = "听得到呀。") -> None:
        self.text = text
        self.requests = []

    async def reply(self, request: BrainRequest) -> BrainReply:
        self.requests.append(request)
        return BrainReply(self.text, model_id="ollama/qwen3.5:9b")


class BlockingBrain(FakeBrain):
    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()

    async def reply(self, request: BrainRequest) -> BrainReply:
        self.requests.append(request)
        self.started.set()
        await asyncio.Event().wait()
        return BrainReply(self.text)


class FakeHandle:
    def __init__(self, state: UtteranceState = UtteranceState.PLAYING) -> None:
        self.state = state
        self.cancelled = False

    def cancel(self) -> bool:
        self.cancelled = True
        self.state = UtteranceState.CANCELLED
        return True

    async def wait(self):
        return type("Outcome", (), {
            "state": (
                UtteranceState.CANCELLED if self.cancelled
                else UtteranceState.FAILED if self.state == UtteranceState.FAILED
                else UtteranceState.FINISHED
            ),
        })()


class FakeVoiceSession:
    def __init__(self, failed: bool = False) -> None:
        self.requests = []
        self.failed = failed
        self.handle = FakeHandle(UtteranceState.FAILED if failed else UtteranceState.PLAYING)

    async def submit(self, request, *, priority):
        del priority
        self.requests.append(request)
        return self.handle


def _service(*, microphone=None, stt=None, brain=None, voice=None) -> NativeVoiceService:
    return NativeVoiceService(
        microphone or FakeMicrophone(), stt or FakeSTT(), brain or FakeBrain(), voice or FakeVoiceSession(),
        session_id="native-test-session",
    )


def test_turn_lifecycle_cleans_temp_audio_and_scopes_brain() -> None:
    async def run() -> None:
        microphone = FakeMicrophone()
        brain = FakeBrain()
        service = _service(microphone=microphone, brain=brain)
        started = await service.start_listening()
        assert started.phase == TurnPhase.LISTENING
        result = await service.stop_listening_and_process()
        assert result.status == TurnStatus.FINISHED
        assert result.transcript is not None
        assert result.reply is not None
        assert microphone.last_path is not None and not microphone.last_path.exists()
        assert brain.requests[0].session_id == "native-test-session"
        assert "native:voice:" not in result.reply.text
        assert [event.phase for event in service.events()] == [
            TurnPhase.LISTENING, TurnPhase.TRANSCRIBING, TurnPhase.THINKING,
            TurnPhase.SPEAKING, TurnPhase.FINISHED,
        ]
        assert all("酸奶" not in str(event.as_dict()) for event in service.events())

    asyncio.run(run())


def test_speaking_blocks_microphone_reentry() -> None:
    async def run() -> None:
        class BlockingHandle(FakeHandle):
            def __init__(self) -> None:
                super().__init__(UtteranceState.PLAYING)
                self.release = asyncio.Event()

            async def wait(self):
                await self.release.wait()
                return await super().wait()

        voice = FakeVoiceSession()
        voice.handle = BlockingHandle()
        service = _service(voice=voice)
        await service.start_listening()
        task = asyncio.create_task(service.stop_listening_and_process())
        for _ in range(20):
            if service.phase == TurnPhase.SPEAKING:
                break
            await asyncio.sleep(0.01)
        assert service.phase == TurnPhase.SPEAKING
        with pytest.raises(NativeVoiceBusyError):
            await service.start_listening()
        voice.handle.release.set()
        assert (await task).status == TurnStatus.FINISHED

    asyncio.run(run())


def test_cancelled_late_brain_result_never_speaks() -> None:
    async def run() -> None:
        brain = BlockingBrain()
        voice = FakeVoiceSession()
        service = _service(brain=brain, voice=voice)
        await service.start_listening()
        task = asyncio.create_task(service.stop_listening_and_process())
        await brain.started.wait()
        assert await service.cancel() is True
        result = await task
        assert result.status == TurnStatus.CANCELLED
        assert voice.requests == []

    asyncio.run(run())


def test_cancel_listening_cleans_audio_without_transcribing() -> None:
    async def run() -> None:
        microphone = FakeMicrophone()
        service = _service(microphone=microphone)
        await service.start_listening()
        assert await service.cancel() is True
        assert microphone.cancelled is True
        assert service.phase == TurnPhase.CANCELLED

    asyncio.run(run())


def test_stt_and_mouth_failures_are_bounded() -> None:
    async def run() -> None:
        service = _service(stt=FailingSTT())
        await service.start_listening()
        result = await service.stop_listening_and_process()
        assert result.status == TurnStatus.FAILED
        assert result.error_code == "empty_transcript"

        service = _service(voice=FakeVoiceSession(failed=True))
        await service.start_listening()
        result = await service.stop_listening_and_process()
        assert result.status == TurnStatus.FAILED
        assert result.error_code == "mouth_failed"

    asyncio.run(run())


@pytest.mark.parametrize("text,language", [("你好", "zh"), ("こんにちは", "ja"), ("hello", "en")])
def test_supported_transcript_languages(text: str, language: str) -> None:
    async def run() -> None:
        service = _service(stt=FakeSTT(text, language))
        await service.start_listening()
        result = await service.stop_listening_and_process()
        assert result.status == TurnStatus.FINISHED
        assert result.transcript is not None and result.transcript.language == language

    asyncio.run(run())


def test_whisper_failure_and_timeout_are_redacted(tmp_path: Path) -> None:
    audio = tmp_path / "input.wav"
    audio.write_bytes(b"wav")
    config = STTConfig("whisper_cpp", Path(sys.executable), Path("/tmp/private-model.bin"), timeout_seconds=1)

    async def failure() -> None:
        with pytest.raises(STTError) as error:
            await WhisperCppSTT(config).transcribe(audio)
        assert str(error.value) == "stt_failed"
        assert "private-model" not in str(error.value)

    asyncio.run(failure())

    class SlowWhisper(WhisperCppSTT):
        def _argv(self, audio_path):
            del audio_path
            return (sys.executable, "-c", "import time; time.sleep(5)")

    async def timeout() -> None:
        slow = SlowWhisper(STTConfig("whisper_cpp", Path(sys.executable), Path("/tmp/model.bin"), timeout_seconds=1))
        with pytest.raises(STTError) as error:
            await slow.transcribe(audio)
        assert str(error.value) == "stt_timeout"

    asyncio.run(timeout())


def test_whisper_empty_output_and_fixed_paths(tmp_path: Path) -> None:
    audio = tmp_path / "input.wav"
    audio.write_bytes(b"wav")

    class EmptyWhisper(WhisperCppSTT):
        def _argv(self, audio_path):
            del audio_path
            return (sys.executable, "-c", "pass")

    config = STTConfig("whisper_cpp", Path("/bin/echo"), Path("/tmp/trusted-model.bin"))
    argv = WhisperCppSTT(config)._argv(audio)
    assert "/bin/echo" == argv[0]
    assert "/tmp/trusted-model.bin" in argv
    # User speech is not an STT configuration input and cannot alter argv.
    assert "--model=/tmp/attacker" not in argv

    async def empty() -> None:
        with pytest.raises(STTError) as error:
            await EmptyWhisper(config).transcribe(audio)
        assert str(error.value) == "empty_transcript"

    asyncio.run(empty())


def test_brain_timeout_and_non_delivery_argv() -> None:
    config = BrainConfig(Path(sys.executable), agent_id="main", timeout_seconds=1)

    class SlowBrain(OpenClawAgentBrain):
        def _argv(self, request):
            del request
            return (sys.executable, "-c", "import time; time.sleep(5)")

    request = BrainRequest("session-1", "turn-1", "hello", "en")
    argv = OpenClawAgentBrain(BrainConfig(Path("/opt/homebrew/bin/openclaw"), agent_id="main"))._argv(request)
    assert "--deliver" not in argv
    assert "--channel" not in argv
    assert "native:voice:session-1" in argv

    async def run() -> None:
        with pytest.raises(BrainError) as error:
            await SlowBrain(config).reply(request)
        assert str(error.value) == "brain_timeout"

    asyncio.run(run())


def test_gateway_payload_envelope_prefers_visible_assistant_text() -> None:
    payload = {
        "payloads": [
            {"role": "assistant", "text": "第一段"},
            {"role": "assistant", "text": "第二段"},
        ],
        "reply": "must-not-win-over-visible-payloads",
        "meta": {"text": "metadata-is-not-a-reply"},
    }
    assert _final_text(payload) == "第一段\n第二段"
    assert _final_text({"reply": "legacy", "result": {"text": "nested"}}) == "legacy"


def test_gateway_payload_envelope_rejects_tool_reasoning_and_metadata() -> None:
    assert _final_text({
        "payloads": [
            {"type": "tool_result", "text": "tool-output"},
            {"type": "tool_use", "text": "tool-use-output"},
            {"type": "reasoning", "text": "private-analysis"},
            {"role": "user", "text": "user-message"},
        ],
        "reply": "must-not-fallback",
    }) is None
    assert _final_text({"meta": {"text": "metadata-only"}}) is None
    with pytest.raises(ValueError):
        BrainReply(_final_text({"payloads": [{"text": "x" * 501}]}))


@pytest.mark.parametrize(
    "text,expected",
    [
        ("こんにちは、酸奶", "ja"),
        ("今天继续做项目", "zh"),
        ("please continue the project", "en"),
        ("hello 世界", "zh"),
        ("hello こんにちは", "ja"),
        ("123 !!!", "zh"),
    ],
)
def test_auto_language_resolution_is_deterministic(text: str, expected: str) -> None:
    assert resolve_transcript_language(text, "auto") == expected
    assert TranscriptResult(text, "auto").language == expected


def test_explicit_transcript_language_is_preserved() -> None:
    assert TranscriptResult("hello", "ja").language == "ja"
    assert TranscriptResult("こんにちは", "en").language == "en"


@pytest.mark.parametrize(
    "text,expected",
    [("こんにちは", "ja"), ("你好", "zh"), ("hello", "en")],
)
def test_real_whisper_adapter_auto_output_resolves_language(tmp_path: Path, text: str, expected: str) -> None:
    audio = tmp_path / "input.wav"
    audio.write_bytes(b"wav")

    class OutputWhisper(WhisperCppSTT):
        def _argv(self, audio_path):
            del audio_path
            return (sys.executable, "-c", "print(%s)" % json.dumps(text))

    async def run() -> None:
        stt = OutputWhisper(STTConfig("whisper_cpp", Path(sys.executable), Path("/tmp/model.bin")))
        result = await stt.transcribe(audio)
        assert result.text == text
        assert result.language == expected

    asyncio.run(run())


@pytest.mark.parametrize(
    "transcript_text,reply_text,expected",
    [
        ("hello", "中文回复", "zh"),
        ("你好", "English reply", "en"),
        ("你好", "日本語の返答", "ja"),
        ("你好", "hello 世界", "zh"),
    ],
)
def test_reply_language_independently_reaches_speak_request(
    transcript_text: str, reply_text: str, expected: str,
) -> None:
    async def run() -> None:
        voice = FakeVoiceSession()
        service = _service(
            stt=FakeSTT(transcript_text, "auto"), brain=FakeBrain(reply_text), voice=voice,
        )
        await service.start_listening()
        assert (await service.stop_listening_and_process()).status == TurnStatus.FINISHED
        assert voice.requests[0].language == expected

    asyncio.run(run())


def test_text_language_resolver_keeps_explicit_request_and_mixed_policy() -> None:
    assert resolve_text_language("hello", "ja") == "ja"
    assert resolve_text_language("hello 世界") == "zh"
    assert resolve_text_language("English と日本語") == "ja"


def test_missing_microphone_backend_is_bounded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    original_import = builtins.__import__

    def unavailable_sounddevice(name, *args, **kwargs):
        if name == "sounddevice":
            raise ImportError("not installed")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", unavailable_sounddevice)
    with pytest.raises(AudioCaptureError, match="^microphone_backend_unavailable$"):
        SoundDeviceMicrophone().start(tmp_path)


def test_memory_is_off_and_native_scope_only() -> None:
    memory = DisabledNativeMemory()
    assert memory.scope("abc") == "native:voice:abc"
    config = NativeVoiceConfig(
        STTConfig("whisper_cpp", Path("/bin/echo"), Path("/tmp/model.bin")),
        BrainConfig(Path("/bin/echo")),
    )
    assert config.memory_mode == "off"
    with pytest.raises(ValueError):
        DisabledNativeMemory("live")


def test_native_package_has_no_wechat_dependency() -> None:
    root = Path(__file__).resolve().parents[1]
    source = "\n".join(path.read_text(encoding="utf-8") for path in root.glob("*.py"))
    assert "wechat" not in source.lower()


def test_openclaw_native_guard_is_present_for_memory_hooks() -> None:
    root = Path(__file__).resolve().parents[2] / "integrations" / "openclaw"
    helper = (root / "sticker-plugin-utils.ts").read_text(encoding="utf-8")
    assert 'value.startsWith("native:voice:")' in helper
    for name in ("index-v4.ts", "v3-runtime.ts", "v3-memory-extended.ts"):
        assert "if (isNativeVoiceContext(ctx, event)) return;" in (root / name).read_text(encoding="utf-8")


def test_toml_loader_is_explicit_on_supported_python_versions() -> None:
    expected = "tomllib" if sys.version_info >= (3, 11) else "tomli"
    assert _toml_loader().__name__ == expected
