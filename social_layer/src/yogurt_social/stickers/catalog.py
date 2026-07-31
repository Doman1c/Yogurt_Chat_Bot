from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .models import StickerCatalogSnapshot, StickerRecord


class StickerCatalogError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


_ID_PATTERN = re.compile(r"^sticker-[a-f0-9]{12,64}$")
_SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")
_ALLOWED_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
_ALLOWED_SCENES = {"casual", "technical", "playful", "comfort", "celebration", "serious"}
_ALLOWED_RELATIONSHIPS = {"new", "familiar", "close", "owner"}
_ALLOWED_STATUSES = {"pending", "approved", "rejected"}
_ALLOWED_WARNINGS = {
    "none",
    "violence",
    "sexual",
    "medical",
    "political",
    "personal_data",
    "offensive_text",
    "copyright_uncertain",
    "unknown_person",
}
_REQUIRED_FIELDS = {
    "id",
    "file",
    "sha256",
    "status",
    "enabled",
    "description",
    "scenes",
    "emotions",
    "tones",
    "keywords",
    "relationships",
    "content_warnings",
    "confidence",
    "classification",
}


def _string_list(
    item: dict[str, Any], key: str, *, allowed: set[str] | None = None
) -> tuple[str, ...]:
    value = item.get(key, [])
    if not isinstance(value, list) or any(not isinstance(entry, str) for entry in value):
        raise StickerCatalogError("invalid_catalog", f"{key} 必须是字符串数组")
    result = tuple(dict.fromkeys(value))
    if allowed is not None and any(entry not in allowed for entry in result):
        raise StickerCatalogError("invalid_catalog", f"{key} 包含不支持的值")
    return result


def _number(
    item: dict[str, Any], key: str, default: float, minimum: float, maximum: float
) -> float:
    value = item.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise StickerCatalogError("invalid_catalog", f"{key} 必须是数字")
    result = float(value)
    if not minimum <= result <= maximum:
        raise StickerCatalogError("invalid_catalog", f"{key} 超出允许范围")
    return result


def _integer(
    item: dict[str, Any], key: str, default: int, minimum: int, maximum: int
) -> int:
    value = item.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise StickerCatalogError("invalid_catalog", f"{key} 必须是合法整数")
    return value


def _safe_asset_path(asset_root: Path, relative_value: Any, field: str) -> tuple[str, Path]:
    if not isinstance(relative_value, str) or not relative_value:
        raise StickerCatalogError("invalid_catalog", f"{field} 必须是非空相对路径")
    relative_path = Path(relative_value)
    if relative_path.is_absolute() or relative_path.parts[:1] != ("images",):
        raise StickerCatalogError("unsafe_asset_path", f"{field} 必须位于 images/ 下")
    if relative_path.suffix.lower() not in _ALLOWED_SUFFIXES:
        raise StickerCatalogError("invalid_catalog", f"{field} 使用了不支持的图片格式")
    candidate = (asset_root / relative_path).resolve()
    try:
        candidate.relative_to(asset_root)
    except ValueError as exc:
        raise StickerCatalogError("unsafe_asset_path", f"{field} 不能逃逸资产目录") from exc
    return relative_value, candidate


class StickerCatalogLoader:
    def __init__(self, catalog_path: str | Path, require_files: bool = True) -> None:
        self.catalog_path = Path(catalog_path).expanduser().resolve()
        self.require_files = require_files

    def load(self) -> StickerCatalogSnapshot:
        try:
            payload = json.loads(self.catalog_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise StickerCatalogError("catalog_unavailable", "无法读取 stickers.json") from exc
        if not isinstance(payload, dict):
            raise StickerCatalogError("invalid_catalog", "Catalog 根节点必须是对象")
        if payload.get("schema_version") != "1.0":
            raise StickerCatalogError("unsupported_catalog_version", "不支持的 Catalog 版本")

        generated_at = payload.get("generated_at")
        if not isinstance(generated_at, str) or not generated_at:
            raise StickerCatalogError("invalid_catalog", "generated_at 必须是非空字符串")
        generated_by = payload.get("generated_by")
        if generated_by is not None and not isinstance(generated_by, str):
            raise StickerCatalogError("invalid_catalog", "generated_by 必须是字符串")

        asset_root_value = payload.get("asset_root", ".")
        if not isinstance(asset_root_value, str) or Path(asset_root_value).is_absolute():
            raise StickerCatalogError("unsafe_asset_root", "asset_root 必须是相对路径")
        asset_root = (self.catalog_path.parent / asset_root_value).resolve()

        raw_stickers = payload.get("stickers")
        if not isinstance(raw_stickers, list):
            raise StickerCatalogError("invalid_catalog", "stickers 必须是数组")

        stickers = tuple(self._parse_sticker(item, asset_root) for item in raw_stickers)
        ids = [sticker.id for sticker in stickers]
        if len(ids) != len(set(ids)):
            raise StickerCatalogError("duplicate_sticker_id", "Catalog 中存在重复 sticker ID")

        return StickerCatalogSnapshot(
            schema_version="1.0",
            generated_at=generated_at,
            generated_by=generated_by,
            source_path=self.catalog_path,
            asset_root=asset_root,
            stickers=stickers,
        )

    def _parse_sticker(self, raw: Any, asset_root: Path) -> StickerRecord:
        if not isinstance(raw, dict):
            raise StickerCatalogError("invalid_catalog", "每条 sticker 记录必须是对象")
        missing = sorted(_REQUIRED_FIELDS - raw.keys())
        if missing:
            raise StickerCatalogError("invalid_catalog", "sticker 缺少必需字段")

        sticker_id = raw.get("id")
        if not isinstance(sticker_id, str) or not _ID_PATTERN.fullmatch(sticker_id):
            raise StickerCatalogError("invalid_catalog", "sticker ID 格式无效")
        sha256 = raw.get("sha256")
        if not isinstance(sha256, str) or not _SHA256_PATTERN.fullmatch(sha256):
            raise StickerCatalogError("invalid_catalog", "sha256 格式无效")

        status = raw.get("status")
        if status not in _ALLOWED_STATUSES:
            raise StickerCatalogError("invalid_catalog", "status 值无效")
        enabled = raw.get("enabled")
        if not isinstance(enabled, bool):
            raise StickerCatalogError("invalid_catalog", "enabled 必须是布尔值")
        description = raw.get("description")
        if not isinstance(description, str) or not description.strip():
            raise StickerCatalogError("invalid_catalog", "description 必须是非空字符串")
        ocr_text = raw.get("ocr_text", "")
        if not isinstance(ocr_text, str):
            raise StickerCatalogError("invalid_catalog", "ocr_text 必须是字符串")
        if not isinstance(raw.get("classification"), dict):
            raise StickerCatalogError("invalid_catalog", "classification 必须是对象")

        file_value, asset_path = _safe_asset_path(asset_root, raw.get("file"), "file")
        if self.require_files and not asset_path.is_file():
            raise StickerCatalogError("asset_missing", "Catalog 引用的图片文件不存在")

        return StickerRecord(
            id=sticker_id,
            file=file_value,
            asset_path=asset_path,
            sha256=sha256,
            status=status,
            enabled=enabled,
            description=description.strip(),
            ocr_text=ocr_text,
            scenes=_string_list(raw, "scenes", allowed=_ALLOWED_SCENES),
            emotions=_string_list(raw, "emotions"),
            tones=_string_list(raw, "tones"),
            keywords=_string_list(raw, "keywords"),
            relationships=_string_list(
                raw, "relationships", allowed=_ALLOWED_RELATIONSHIPS
            ),
            avoid_when=_string_list(raw, "avoid_when"),
            content_warnings=_string_list(
                raw, "content_warnings", allowed=_ALLOWED_WARNINGS
            ),
            weight=_number(raw, "weight", 1.0, 0.0, 5.0),
            cooldown_seconds=_integer(raw, "cooldown_seconds", 1800, 0, 604800),
            confidence=_number(raw, "confidence", 0.0, 0.0, 1.0),
        )
