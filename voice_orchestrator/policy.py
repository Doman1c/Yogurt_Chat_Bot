from __future__ import annotations

from dataclasses import dataclass

from voice_runtime.schemas import Emotion, SpeechRequest

from .schemas import Scene, VoicePlanInput


class VoicePlanError(ValueError):
    pass


@dataclass(frozen=True)
class ScenePreset:
    emotion: Emotion
    intensity: float


_SCENE_PRESETS: dict[Scene, ScenePreset] = {
    "casual": ScenePreset("neutral", 0.55),
    "technical": ScenePreset("neutral", 0.45),
    "playful": ScenePreset("happy", 0.72),
    "comfort": ScenePreset("soft", 0.55),
    "celebration": ScenePreset("happy", 0.82),
    "serious": ScenePreset("neutral", 0.50),
}


def normalize_spoken_text(text: str) -> str:
    lines = [line.strip() for line in text.strip().splitlines()]
    normalized = "\n".join(line for line in lines if line)
    if not normalized:
        raise VoicePlanError("empty_text")
    if len(normalized) > 500:
        raise VoicePlanError("text_too_long_for_p10_b0")
    return normalized


def plan_voice(plan: VoicePlanInput) -> SpeechRequest:
    preset = _SCENE_PRESETS[plan.scene]
    emotion = plan.mood or preset.emotion

    return SpeechRequest(
        text=normalize_spoken_text(plan.text),
        voice="yogurt",
        language=plan.language,
        emotion=emotion,
        intensity=preset.intensity,
        # Keep speed unset so the accepted Voice Profile default is authoritative.
        speed=None,
        stream=False,
    )
