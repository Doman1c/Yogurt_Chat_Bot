"""Private, startup-only reference registry validation for Yogurt TTS."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


CANONICAL_EMOTIONS = ("neutral", "happy", "excited", "soft", "shy", "upset", "sad", "serious", "comforting")
ALIASES = {"angry": "upset", "surprised": "excited"}


@dataclass(frozen=True)
class Route:
    requested: str
    canonical: str
    used: str
    fallback: bool
    reference_id: str
    role: str
    selection_mode: str
    fallback_reason: str | None
    audio: Path
    prompt_text: str
    prompt_language: str

    @property
    def selection(self) -> str:  # compatibility with the first V2 debug response
        return self.selection_mode


class EmotionRegistry:
    def __init__(self, path: Path, reference_root: Path | None = None) -> None:
        self.path = path.resolve()
        self.reference_root = (reference_root or self.path.parent).resolve()
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        references = raw.get("references")
        if not isinstance(references, list) or not references:
            raise ValueError("emotion registry references must be a non-empty list")
        self.references: dict[str, dict[str, Any]] = {}
        self.validation_rows: list[dict[str, object]] = []
        for item in references:
            ref_id = str(item.get("id", "")).strip()
            raw_audio = str(item.get("audio", "")).strip()
            candidate = Path(raw_audio)
            audio = (candidate if candidate.is_absolute() else self.reference_root / candidate).resolve()
            prompt = str(item.get("prompt_text", "")).strip()
            language = str(item.get("prompt_lang", "")).lower()
            enabled = bool(item.get("enabled"))
            self.validation_rows.append({"reference_id": ref_id, "json_audio": raw_audio, "resolved_path": str(audio),
                                         "exists": audio.is_file(), "prompt_present": bool(prompt), "prompt_lang": language,
                                         "enabled": enabled})
            if not ref_id or ref_id in self.references:
                raise ValueError(f"invalid or duplicate reference id: {ref_id or '<missing>'}")
            if enabled and (not audio.is_file() or not prompt or language != "ja"):
                raise ValueError(f"invalid enabled reference: {ref_id}")
            self.references[ref_id] = {**item, "audio": audio, "prompt_text": prompt, "prompt_lang": language}
        routing_raw = raw.get("routing_index")
        if not isinstance(routing_raw, dict):
            raise ValueError("emotion registry routing_index must be a mapping")
        self.routing = routing_raw.get("emotions", routing_raw)
        if not isinstance(self.routing, dict):
            raise ValueError("emotion registry routing_index.emotions must be a mapping")
        self.missing = {str(item) for item in raw.get("missing_emotions", [])}
        if "neutral" not in self.routing:
            raise ValueError("emotion registry must declare neutral routing")
        for emotion, group in self.routing.items():
            if not isinstance(group, dict):
                raise ValueError(f"routing for {emotion} must be a mapping")
            for role in ("primary", "secondary"):
                rows = group.get(role, [])
                if not isinstance(rows, list):
                    raise ValueError(f"routing {emotion}.{role} must be a list")
                for row in rows:
                    ref_id = str(row.get("id", "")) if isinstance(row, dict) else ""
                    if ref_id not in self.references:
                        raise ValueError(f"routing references unknown id: {ref_id or '<missing>'}")
        if not self._candidates("neutral"):
            raise ValueError("emotion registry requires an enabled neutral reference")
        for emotion in self.routing:
            if emotion not in self.missing and not self._candidates(emotion):
                raise ValueError(f"declared available emotion has no enabled reference: {emotion}")

    def _candidates(self, emotion: str) -> list[tuple[dict[str, Any], str]]:
        group = self.routing.get(emotion, {})
        result: list[tuple[dict[str, Any], str]] = []
        for role in ("primary", "secondary"):
            for row in group.get(role, []):
                ref_id = str(row.get("id", ""))
                item = self.references[ref_id]
                if item.get("enabled"):
                    result.append((item, role))
        return result

    @staticmethod
    def _ranked(candidates: list[tuple[dict[str, Any], str]]) -> list[tuple[dict[str, Any], str]]:
        return sorted(candidates, key=lambda pair: (float(pair[0].get("human_intensity_hint", 0.5)),
                                                    0 if pair[1] == "primary" else 1, str(pair[0]["id"])))

    def route(self, requested: str, *, intensity: float | None = None) -> Route:
        normalized = requested.strip().lower() or "neutral"
        canonical = ALIASES.get(normalized, normalized)
        fallback_reason: str | None = None
        if canonical not in CANONICAL_EMOTIONS:
            canonical, fallback_reason = "neutral", "unknown_emotion"
        used = canonical
        if canonical in self.missing:
            used, fallback_reason = "neutral", f"no_true_{canonical}_reference"
        candidates = self._candidates(used)
        if not candidates:
            raise ValueError(f"no enabled reference for {used}")
        primary = next((candidate for candidate in candidates if candidate[1] == "primary"), candidates[0])
        selected, role, selection_mode = primary[0], primary[1], "primary_default"
        if intensity is not None and len(candidates) > 1:
            calibrated = [(item, item_role) for item, item_role in candidates if item.get("runtime_intensity") is not None]
            if len(calibrated) >= 2:
                selected, role = min(calibrated, key=lambda pair: (abs(float(pair[0]["runtime_intensity"]) - intensity),
                                                                    0 if pair[1] == "primary" else 1))
                selection_mode = "runtime_calibrated"
            else:
                ranked = self._ranked(candidates)
                selected, role = ranked[round(intensity * (len(ranked) - 1))]
                selection_mode = "human_rank_provisional"
        return Route(requested=normalized, canonical=canonical, used=used, fallback=used != canonical,
                     reference_id=str(selected["id"]), role=role, selection_mode=selection_mode,
                     fallback_reason=fallback_reason, audio=Path(selected["audio"]),
                     prompt_text=str(selected["prompt_text"]), prompt_language=str(selected["prompt_lang"]))

    def public_profiles(self) -> list[dict[str, object]]:
        profiles: list[dict[str, object]] = []
        for emotion in CANONICAL_EMOTIONS:
            candidates = self._candidates(emotion) if emotion in self.routing else []
            profiles.append({"id": emotion, "available": emotion not in self.missing and bool(candidates),
                             "primary_count": sum(role == "primary" for _, role in candidates),
                             "secondary_count": sum(role == "secondary" for _, role in candidates),
                             "intensity_mode": "provisional",
                             **({"fallback": "neutral"} if emotion in self.missing else {})})
        return profiles

    def write_validation_report(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = ["# Reference validation report", "", f"Registry: {self.path}", f"Reference root: {self.reference_root}", "",
                 "| reference_id | JSON audio | resolved path | exists | prompt | prompt_lang | enabled |",
                 "|---|---|---|---:|---:|---|---:|"]
        for row in self.validation_rows:
            lines.append("| {reference_id} | {json_audio} | {resolved_path} | {exists} | {prompt_present} | {prompt_lang} | {enabled} |".format(**row))
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
