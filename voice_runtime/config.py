from __future__ import annotations

import ipaddress
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional, Tuple, Union
from urllib.parse import urlparse

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover - exercised on Python 3.10 runtime
    import tomli as tomllib  # type: ignore[no-redef]


class ConfigError(ValueError):
    pass


def _is_loopback_host(host: str) -> bool:
    normalized = host.strip().lower()
    if normalized == "localhost":
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def _validate_loopback_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme != "http":
        raise ConfigError("engine.base_url must use http on loopback")
    if not parsed.hostname or not _is_loopback_host(parsed.hostname):
        raise ConfigError("engine.base_url must point to loopback")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ConfigError("engine.base_url must not contain credentials/query/fragment")
    return value.rstrip("/")


@dataclass(frozen=True)
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = 8770


@dataclass(frozen=True)
class EngineConfig:
    type: str = "yogurt_tts"
    base_url: str = "http://127.0.0.1:17861"
    timeout_seconds: float = 180.0


@dataclass(frozen=True)
class VoiceProfile:
    name: str
    reference_path: str
    reference_text: str
    prompt_language: str = "ja"
    extra_reference_paths: Tuple[str, ...] = ()
    zh_speed: float = 1.13
    ja_speed: float = 1.02
    top_k: int = 15
    top_p: float = 1.0
    temperature: float = 1.0


@dataclass(frozen=True)
class LimitsConfig:
    max_text_chars: int = 500
    max_request_seconds: int = 180
    max_concurrent_requests: int = 1


@dataclass(frozen=True)
class RuntimeConfig:
    server: ServerConfig
    engine: EngineConfig
    limits: LimitsConfig
    default_profile: str
    profiles: Mapping[str, VoiceProfile]


def _table(data: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = data.get(key, {})
    if not isinstance(value, Mapping):
        raise ConfigError(f"{key} must be a TOML table")
    return value


def load_config(
    path: Optional[Union[str, os.PathLike[str]]] = None,
) -> RuntimeConfig:
    config_path = Path(
        path
        or os.environ.get("YOGURT_VOICE_CONFIG", "")
        or Path(__file__).with_name("config.toml")
    )
    if not config_path.is_file():
        raise ConfigError(
            f"Voice Runtime config not found. Copy config.example.toml to config.toml: {config_path}"
        )

    # Windows PowerShell 5.1 `Set-Content -Encoding utf8` writes a UTF-8 BOM.
    # Decode with utf-8-sig so both BOM and non-BOM local config files are accepted.
    try:
        raw = tomllib.loads(config_path.read_bytes().decode("utf-8-sig"))
    except UnicodeDecodeError as exc:
        raise ConfigError("Voice Runtime config must be UTF-8") from exc

    server_raw = _table(raw, "server")
    server = ServerConfig(
        host=str(server_raw.get("host", "127.0.0.1")),
        port=int(server_raw.get("port", 8770)),
    )
    if not _is_loopback_host(server.host):
        raise ConfigError("server.host must be loopback")
    if not (1 <= server.port <= 65535):
        raise ConfigError("server.port is invalid")

    engine_raw = _table(raw, "engine")
    engine_type = str(engine_raw.get("type", "yogurt_tts"))
    legacy_gpt_config = engine_type == "gpt_sovits"
    # Older local files pointed at GPT-SoVITS api.py on 9880.  Silently keeping
    # that transport would leak private reference routing out of the service;
    # treat it as a compatibility spelling for the 17861 gateway instead.
    if engine_type == "gpt_sovits":
        engine_type = "yogurt_tts"
    if engine_type != "yogurt_tts":
        raise ConfigError("engine.type must be 'yogurt_tts'")
    engine = EngineConfig(
        type=engine_type,
        base_url=_validate_loopback_url(
            "http://127.0.0.1:17861" if legacy_gpt_config else str(engine_raw.get("base_url", "http://127.0.0.1:17861"))
        ),
        timeout_seconds=float(engine_raw.get("timeout_seconds", 180)),
    )
    if not (1 <= engine.timeout_seconds <= 600):
        raise ConfigError("engine.timeout_seconds must be between 1 and 600")

    limits_raw = _table(raw, "limits")
    limits = LimitsConfig(
        max_text_chars=int(limits_raw.get("max_text_chars", 500)),
        max_request_seconds=int(limits_raw.get("max_request_seconds", 180)),
        max_concurrent_requests=int(limits_raw.get("max_concurrent_requests", 1)),
    )
    if not (1 <= limits.max_text_chars <= 4000):
        raise ConfigError("limits.max_text_chars is invalid")
    if limits.max_concurrent_requests != 1:
        raise ConfigError("P10-A3 requires max_concurrent_requests=1")

    voice_raw = _table(raw, "voice")
    default_profile = str(voice_raw.get("default_profile", "yogurt"))

    profiles_raw = _table(raw, "profiles")
    profiles: dict[str, VoiceProfile] = {}
    for profile_name, value in profiles_raw.items():
        if not isinstance(value, Mapping):
            raise ConfigError(f"profiles.{profile_name} must be a TOML table")
        reference_path = str(value.get("reference_path", "")).strip()
        reference_text = str(value.get("reference_text", "")).strip()
        if not reference_path or not reference_text:
            raise ConfigError(
                f"profiles.{profile_name} requires reference_path and reference_text"
            )
        prompt_language = str(value.get("prompt_language", "ja")).lower()
        if prompt_language not in {"ja", "zh"}:
            raise ConfigError(f"profiles.{profile_name}.prompt_language is unsupported")
        extra_refs = value.get("extra_reference_paths", [])
        if not isinstance(extra_refs, list) or not all(isinstance(x, str) for x in extra_refs):
            raise ConfigError(
                f"profiles.{profile_name}.extra_reference_paths must be a string array"
            )
        profiles[profile_name] = VoiceProfile(
            name=profile_name,
            reference_path=reference_path,
            reference_text=reference_text,
            prompt_language=prompt_language,
            extra_reference_paths=tuple(extra_refs),
            zh_speed=float(value.get("zh_speed", 1.13)),
            ja_speed=float(value.get("ja_speed", 1.02)),
            top_k=int(value.get("top_k", 15)),
            top_p=float(value.get("top_p", 1.0)),
            temperature=float(value.get("temperature", 1.0)),
        )

    if profiles and default_profile not in profiles:
        raise ConfigError("voice.default_profile does not exist in [profiles]")

    return RuntimeConfig(
        server=server,
        engine=engine,
        limits=limits,
        default_profile=default_profile,
        profiles=profiles,
    )
