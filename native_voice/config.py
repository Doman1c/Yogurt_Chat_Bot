"""Trusted local-only configuration for Native Voice."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict

from .schemas import normalize_language


class ConfigError(ValueError):
    """A bounded configuration failure that never exposes local paths."""


@dataclass(frozen=True)
class STTConfig:
    backend: str
    executable: Path
    model: Path
    language: str = "auto"
    timeout_seconds: float = 45.0
    max_stdout_bytes: int = 65536

    def __post_init__(self) -> None:
        if self.backend != "whisper_cpp":
            raise ConfigError("unsupported_stt_backend")
        if not self.executable.is_absolute() or not self.model.is_absolute():
            raise ConfigError("stt_paths_must_be_absolute")
        object.__setattr__(self, "language", normalize_language(self.language))
        if not 1 <= self.timeout_seconds <= 300:
            raise ConfigError("invalid_stt_timeout")
        if not 1024 <= self.max_stdout_bytes <= 1024 * 1024:
            raise ConfigError("invalid_stt_stdout_limit")


@dataclass(frozen=True)
class BrainConfig:
    executable: Path
    agent_id: str = "main"
    timeout_seconds: float = 90.0
    max_stdout_bytes: int = 262144

    def __post_init__(self) -> None:
        if not self.executable.is_absolute():
            raise ConfigError("brain_executable_must_be_absolute")
        if not self.agent_id or len(self.agent_id) > 64:
            raise ConfigError("invalid_brain_agent")
        if not 1 <= self.timeout_seconds <= 600:
            raise ConfigError("invalid_brain_timeout")
        if not 1024 <= self.max_stdout_bytes <= 1024 * 1024:
            raise ConfigError("invalid_brain_stdout_limit")


@dataclass(frozen=True)
class NativeVoiceConfig:
    stt: STTConfig
    brain: BrainConfig
    memory_mode: str = "off"
    sink_id: str = "macos"

    def __post_init__(self) -> None:
        # P11-A1 deliberately cannot opt into the live B.7 Memory Service.
        if self.memory_mode != "off":
            raise ConfigError("native_memory_not_available")
        if self.sink_id != "macos":
            raise ConfigError("invalid_native_sink")


def _toml_loader():
    try:
        import tomllib  # type: ignore
        return tomllib
    except ImportError:
        try:
            import tomli  # type: ignore
            return tomli
        except ImportError as error:
            raise ConfigError("toml_parser_unavailable") from error


def _section(value: Any, name: str) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigError("invalid_config_section")
    section = value.get(name)
    if not isinstance(section, dict):
        raise ConfigError("missing_config_section")
    return section


def _path(section: Dict[str, Any], name: str) -> Path:
    value = section.get(name)
    if not isinstance(value, str) or not value:
        raise ConfigError("missing_config_value")
    return Path(value).expanduser()


def load_config(path: str | None = None) -> NativeVoiceConfig:
    config_path = Path(path or "native_voice/config.toml").expanduser()
    if not config_path.is_file():
        raise ConfigError("native_voice_config_not_found")
    try:
        with config_path.open("rb") as handle:
            raw = _toml_loader().load(handle)
    except (OSError, ValueError, TypeError) as error:
        raise ConfigError("native_voice_config_invalid") from error
    stt = _section(raw, "stt")
    brain = _section(raw, "brain")
    memory = raw.get("memory", {})
    voice = raw.get("voice", {})
    if not isinstance(memory, dict) or not isinstance(voice, dict):
        raise ConfigError("invalid_config_section")
    config = NativeVoiceConfig(
        stt=STTConfig(
            backend=str(stt.get("backend", "")), executable=_path(stt, "executable"),
            model=_path(stt, "model"), language=str(stt.get("language", "auto")),
            timeout_seconds=float(stt.get("timeout_seconds", 45)),
        ),
        brain=BrainConfig(
            executable=_path(brain, "executable"), agent_id=str(brain.get("agent_id", "main")),
            timeout_seconds=float(brain.get("timeout_seconds", 90)),
        ),
        memory_mode=str(memory.get("mode", "off")), sink_id=str(voice.get("sink_id", "macos")),
    )
    if not config.stt.executable.is_file() or not config.stt.model.is_file():
        raise ConfigError("stt_path_unavailable")
    if not config.brain.executable.is_file():
        raise ConfigError("brain_executable_unavailable")
    return config
