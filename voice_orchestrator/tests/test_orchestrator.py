import pytest
from pydantic import ValidationError

from voice_orchestrator.client import VoiceRuntimeError, validate_runtime_url
from voice_orchestrator.policy import VoicePlanError, plan_voice
from voice_orchestrator.schemas import VoicePlanInput


def test_casual_plan_keeps_runtime_speed_default() -> None:
    request = plan_voice(VoicePlanInput(text="主人，你回来啦。", scene="casual"))
    assert request.voice == "yogurt"
    assert request.language == "zh"
    assert request.emotion == "neutral"
    assert request.speed is None
    assert request.stream is False


def test_scene_policy_maps_playful_and_comfort() -> None:
    playful = plan_voice(VoicePlanInput(text="嘿嘿。", scene="playful"))
    comfort = plan_voice(VoicePlanInput(text="没关系，我在。", scene="comfort"))
    assert playful.emotion == "happy"
    assert playful.intensity == pytest.approx(0.72)
    assert comfort.emotion == "soft"
    assert comfort.intensity == pytest.approx(0.55)


def test_bounded_mood_can_override_scene_emotion() -> None:
    request = plan_voice(
        VoicePlanInput(text="今天辛苦啦。", scene="casual", mood="soft")
    )
    assert request.emotion == "soft"
    assert request.speed is None


def test_expanded_canonical_mood_is_accepted() -> None:
    request = plan_voice(VoicePlanInput(text="太棒啦！", scene="casual", mood="excited"))
    assert request.emotion == "excited"


def test_unknown_scene_or_mood_is_rejected() -> None:
    with pytest.raises(ValidationError):
        VoicePlanInput(text="hello", scene="other")
    with pytest.raises(ValidationError):
        VoicePlanInput(text="hello", mood="ecstatic")


def test_long_text_is_not_silently_truncated() -> None:
    with pytest.raises(VoicePlanError, match="text_too_long_for_p10_b0"):
        plan_voice(VoicePlanInput(text="长" * 501))


def test_runtime_url_must_be_loopback() -> None:
    assert validate_runtime_url("http://127.0.0.1:8770") == "http://127.0.0.1:8770"
    assert validate_runtime_url("http://localhost:8770") == "http://localhost:8770"
    with pytest.raises(VoiceRuntimeError, match="voice_runtime_url_must_be_loopback"):
        validate_runtime_url("http://192.168.1.20:8770")
    with pytest.raises(VoiceRuntimeError, match="voice_runtime_url_must_use_http"):
        validate_runtime_url("https://127.0.0.1:8770")
