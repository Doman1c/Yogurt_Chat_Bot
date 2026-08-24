from __future__ import annotations

import asyncio

import pytest
from pydantic import ValidationError

from capabilities.mouth import MouthService, SpeakRequest
from voice_orchestrator.client import SynthesizedAudio, VoiceRuntimeError


class FakeVoiceClient:
    def __init__(self, result: SynthesizedAudio | Exception) -> None:
        self.result = result
        self.requests = []

    async def synthesize(self, request):
        self.requests.append(request)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def _audio() -> SynthesizedAudio:
    return SynthesizedAudio(
        audio=b"RIFFtest",
        media_type="audio/wav",
        voice="yogurt",
        language="zh",
        speed="1.130",
    )


def test_valid_casual_request_uses_existing_orchestrator_policy() -> None:
    client = FakeVoiceClient(_audio())
    result = asyncio.run(MouthService(client).speak(SpeakRequest(text="主人，欢迎回来。")))

    assert result.success is True
    assert result.audio == b"RIFFtest"
    assert result.media_type == "audio/wav"
    assert result.voice == "yogurt"
    assert client.requests[0].emotion == "neutral"
    assert client.requests[0].intensity == pytest.approx(0.55)
    assert client.requests[0].speed is None


def test_valid_playful_request_reuses_scene_policy() -> None:
    client = FakeVoiceClient(_audio())
    result = asyncio.run(
        MouthService(client).speak(SpeakRequest(text="嘿嘿！", scene="playful"))
    )

    assert result.success is True
    assert result.scene == "playful"
    assert client.requests[0].emotion == "happy"
    assert client.requests[0].intensity == pytest.approx(0.72)


def test_empty_and_oversized_text_are_rejected() -> None:
    with pytest.raises(ValidationError):
        SpeakRequest(text="   ")
    with pytest.raises(ValidationError):
        SpeakRequest(text="长" * 501)


def test_invalid_scene_and_caller_endpoint_are_rejected() -> None:
    with pytest.raises(ValidationError):
        SpeakRequest(text="hello", scene="unknown")
    with pytest.raises(ValidationError):
        SpeakRequest(text="hello", endpoint="http://192.168.1.20:8770")


def test_orchestrator_failure_is_bounded_without_raw_body_leakage() -> None:
    private_error = VoiceRuntimeError(
        "upstream failed at /Users/yogurt/private/refs/yogurt.wav: secret detail"
    )
    result = asyncio.run(
        MouthService(FakeVoiceClient(private_error)).speak(SpeakRequest(text="测试。"))
    )

    assert result.success is False
    assert result.error_code == "synthesis_failed"
    assert "private" not in result.model_dump_json()
    assert "secret" not in result.model_dump_json()


def test_unreachable_and_timeout_are_normalized() -> None:
    unreachable = asyncio.run(
        MouthService(FakeVoiceClient(VoiceRuntimeError("voice_runtime_unreachable"))).speak(
            SpeakRequest(text="测试。")
        )
    )
    timeout = asyncio.run(
        MouthService(FakeVoiceClient(VoiceRuntimeError("voice_runtime_timeout"))).speak(
            SpeakRequest(text="测试。")
        )
    )

    assert unreachable.error_code == "voice_runtime_unreachable"
    assert timeout.error_code == "timeout"


def test_request_exposes_no_model_or_reference_path_controls() -> None:
    fields = SpeakRequest.model_fields
    assert set(fields) == {"text", "scene", "language"}
    assert "reference_path" not in fields
    assert "model_path" not in fields
