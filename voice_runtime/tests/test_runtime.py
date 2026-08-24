from pathlib import Path

import pytest
from pydantic import ValidationError

from voice_runtime.config import ConfigError, EngineConfig, VoiceProfile, load_config
from voice_runtime.engines.gpt_sovits import GptSovitsAdapter
from voice_runtime.schemas import SpeechRequest


def _write_config(tmp_path: Path, server_host: str = "127.0.0.1", engine_url: str = "http://127.0.0.1:9880") -> Path:
    path = tmp_path / "config.toml"
    path.write_text(
        f'''[server]
host = "{server_host}"
port = 8770

[engine]
type = "gpt_sovits"
base_url = "{engine_url}"
timeout_seconds = 180

[voice]
default_profile = "yogurt"

[profiles.yogurt]
reference_path = "E:/private/reference.wav"
reference_text = "reference text"
prompt_language = "ja"
extra_reference_paths = ["E:/private/extra1.wav", "E:/private/extra2.wav"]
zh_speed = 1.10
ja_speed = 1.02
top_k = 15
top_p = 1.0
temperature = 1.0

[limits]
max_text_chars = 500
max_request_seconds = 180
max_concurrent_requests = 1
''',
        encoding="utf-8",
    )
    return path


def test_config_requires_loopback_server(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="server.host must be loopback"):
        load_config(_write_config(tmp_path, server_host="0.0.0.0"))


def test_config_requires_loopback_engine(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="engine.base_url must point to loopback"):
        load_config(_write_config(tmp_path, engine_url="http://192.168.1.10:9880"))


def test_payload_uses_fixed_profile_references_and_language_speed() -> None:
    profile = VoiceProfile(
        name="yogurt",
        reference_path="E:/private/main.wav",
        reference_text="キリト君の手、なんだかあったかいね。",
        prompt_language="ja",
        extra_reference_paths=("E:/private/a.wav", "E:/private/b.wav"),
        zh_speed=1.10,
        ja_speed=1.02,
    )
    adapter = GptSovitsAdapter(EngineConfig())

    zh_payload, zh_speed = adapter.build_payload(
        text="主人，你回来啦。",
        language="zh",
        profile=profile,
        speed=None,
    )
    ja_payload, ja_speed = adapter.build_payload(
        text="おかえりなさい。",
        language="ja",
        profile=profile,
        speed=None,
    )

    assert zh_speed == pytest.approx(1.10)
    assert ja_speed == pytest.approx(1.02)
    assert zh_payload["refer_wav_path"] == "E:/private/main.wav"
    assert zh_payload["prompt_language"] == "ja"
    assert zh_payload["inp_refs"] == ["E:/private/a.wav", "E:/private/b.wav"]
    assert zh_payload["text_language"] == "zh"
    assert ja_payload["text_language"] == "ja"


def test_request_does_not_accept_arbitrary_voice_or_streaming() -> None:
    with pytest.raises(ValidationError):
        SpeechRequest(text="hello", voice="../../other")
    with pytest.raises(ValidationError):
        SpeechRequest(text="hello", stream=True)


def test_request_speed_is_bounded() -> None:
    with pytest.raises(ValidationError):
        SpeechRequest(text="hello", speed=2.0)
