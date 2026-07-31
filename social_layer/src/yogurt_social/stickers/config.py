from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class StickerConfigError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class StickerRuntimeConfig:
    schema_version: str
    enabled: bool
    catalog_path: str
    minimum_score: float
    max_candidates: int
    require_files: bool
    global_cooldown_seconds: int
    scene_cooldown_seconds: int
    default_sticker_cooldown_seconds: int
    max_stickers_per_hour: int
    allowed_channels: tuple[str, ...]

    def resolve_catalog_path(self, project_root: str | Path) -> Path:
        candidate = Path(self.catalog_path).expanduser()
        if candidate.is_absolute():
            return candidate.resolve()
        return (Path(project_root).resolve() / candidate).resolve()


def _require_bool(data: dict[str, Any], key: str, default: bool) -> bool:
    value = data.get(key, default)
    if not isinstance(value, bool):
        raise StickerConfigError("invalid_config", f"{key} 必须是布尔值")
    return value


def _require_int(
    data: dict[str, Any], key: str, default: int, minimum: int, maximum: int
) -> int:
    value = data.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise StickerConfigError(
            "invalid_config", f"{key} 必须是 {minimum} 到 {maximum} 之间的整数"
        )
    return value


def _require_number(
    data: dict[str, Any], key: str, default: float, minimum: float, maximum: float
) -> float:
    value = data.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise StickerConfigError("invalid_config", f"{key} 必须是数字")
    result = float(value)
    if not minimum <= result <= maximum:
        raise StickerConfigError(
            "invalid_config", f"{key} 必须在 {minimum} 到 {maximum} 之间"
        )
    return result


def load_sticker_runtime_config(path: str | Path) -> StickerRuntimeConfig:
    config_path = Path(path)
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StickerConfigError("config_unavailable", "无法读取表情包运行时配置") from exc
    if not isinstance(payload, dict):
        raise StickerConfigError("invalid_config", "表情包运行时配置根节点必须是对象")

    schema_version = payload.get("schema_version", "1.0")
    if schema_version != "1.0":
        raise StickerConfigError("unsupported_config_version", "不支持的表情包运行时配置版本")

    catalog_path = payload.get("catalog_path", "assets/stickers/stickers.json")
    if not isinstance(catalog_path, str) or not catalog_path.strip():
        raise StickerConfigError("invalid_config", "catalog_path 必须是非空字符串")

    allowed_channels = payload.get("allowed_channels", ["ilink"])
    if not isinstance(allowed_channels, list) or any(
        not isinstance(channel, str) or not channel for channel in allowed_channels
    ):
        raise StickerConfigError("invalid_config", "allowed_channels 必须是字符串数组")

    return StickerRuntimeConfig(
        schema_version="1.0",
        enabled=_require_bool(payload, "enabled", False),
        catalog_path=catalog_path,
        minimum_score=_require_number(payload, "minimum_score", 5.0, 0.0, 100.0),
        max_candidates=_require_int(payload, "max_candidates", 5, 1, 20),
        require_files=_require_bool(payload, "require_files", True),
        global_cooldown_seconds=_require_int(
            payload, "global_cooldown_seconds", 300, 0, 86400
        ),
        scene_cooldown_seconds=_require_int(
            payload, "scene_cooldown_seconds", 900, 0, 86400
        ),
        default_sticker_cooldown_seconds=_require_int(
            payload, "default_sticker_cooldown_seconds", 1800, 0, 604800
        ),
        max_stickers_per_hour=_require_int(payload, "max_stickers_per_hour", 4, 0, 60),
        allowed_channels=tuple(dict.fromkeys(allowed_channels)),
    )
