from pathlib import Path

import pytest
from pydantic import ValidationError

from voice_runtime.config import ConfigError, EngineConfig, load_config
from voice_runtime.engines.yogurt_tts import YogurtTtsAdapter
from voice_runtime.schemas import SpeechRequest


def _write_config(
    tmp_path: Path,
    server_host: str = "127.0.0.1",
    engine_url: str = "http://127.0.0.1:17861",
    encoding: str = "utf-8",
) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(
        f'''[server]
host = "{server_host}"
port = 8770

[engine]
type = "yogurt_tts"
base_url = "{engine_url}"
timeout_seconds = 180

[voice]
default_profile = "yogurt"

[profiles.yogurt]
reference_path = "E:/private/reference.wav"
reference_text = "reference text"
prompt_language = "ja"
extra_reference_paths = ["E:/private/extra1.wav", "E:/private/extra2.wav"]
zh_speed = 1.13
ja_speed = 1.02
top_k = 15
top_p = 1.0
temperature = 1.0

[limits]
max_text_chars = 500
max_request_seconds = 180
max_concurrent_requests = 1
''',
        encoding=encoding,
    )
    return path


def test_config_accepts_utf8_bom(tmp_path: Path) -> None:
    config = load_config(_write_config(tmp_path, encoding="utf-8-sig"))
    assert config.default_profile == "yogurt"
    assert config.profiles["yogurt"].zh_speed == pytest.approx(1.13)


def test_config_requires_loopback_server(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="server.host must be loopback"):
        load_config(_write_config(tmp_path, server_host="0.0.0.0"))


def test_config_requires_loopback_engine(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="engine.base_url must point to loopback"):
        load_config(_write_config(tmp_path, engine_url="http://192.168.1.10:9880"))


def test_gateway_payload_has_no_private_references_and_omits_default_intensity() -> None:
    adapter = YogurtTtsAdapter(EngineConfig())
    payload = adapter.build_payload(
        text="主人，你回来啦。",
        language="zh",
        emotion="happy",
        intensity=None,
    )
    assert payload == {"text": "主人，你回来啦。", "language": "zh", "emotion": "happy"}
    assert YogurtTtsAdapter.build_payload(text="x", language="ja", emotion="soft", intensity=0.3)["intensity"] == 0.3


def test_legacy_gpt_config_is_forced_to_local_yogurt_service(tmp_path: Path) -> None:
    path = _write_config(tmp_path, engine_url="http://127.0.0.1:9880")
    path.write_text(path.read_text(encoding="utf-8").replace('type = "yogurt_tts"', 'type = "gpt_sovits"'), encoding="utf-8")
    config = load_config(path)
    assert config.engine.type == "yogurt_tts"
    assert config.engine.base_url == "http://127.0.0.1:17861"


def test_request_does_not_accept_arbitrary_voice_or_streaming() -> None:
    with pytest.raises(ValidationError):
        SpeechRequest(text="hello", voice="../../other")
    with pytest.raises(ValidationError):
        SpeechRequest(text="hello", stream=True)


def test_request_speed_is_bounded() -> None:
    with pytest.raises(ValidationError):
        SpeechRequest(text="hello", speed=2.0)


def test_request_covers_canonical_languages_and_optional_intensity() -> None:
    assert SpeechRequest(text="hello", language="en", emotion="excited").intensity is None
    with pytest.raises(ValidationError):
        SpeechRequest(text="hello", intensity=1.1)
