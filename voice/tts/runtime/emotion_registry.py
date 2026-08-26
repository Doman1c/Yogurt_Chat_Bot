"""Private, startup-only reference registry validation for Yogurt TTS."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


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
    selection: str
    audio: Path
    prompt_text: str
    prompt_language: str


class EmotionRegistry:
    def __init__(self, path: Path) -> None:
        raw = json.loads(path.read_text(encoding="utf-8"))
        references = raw.get("references")
        if not isinstance(references, list) or len(references) != 48:
            raise ValueError("emotion registry must contain exactly 48 references")
        self.references: dict[str, dict] = {}
        for item in references:
            ref_id = str(item.get("id", ""))
            audio = Path(str(item.get("audio", "")))
            prompt = str(item.get("prompt_text", "")).strip()
            language = str(item.get("prompt_lang", "")).lower()
            if not ref_id or not audio.is_file() or not prompt or language not in {"ja", "en"} or not item.get("enabled"):
                raise ValueError(f"invalid enabled reference: {ref_id or '<missing>'}")
            self.references[ref_id] = item
        if len(self.references) != 48:
            raise ValueError("emotion registry reference ids must be unique")
        self.routing = raw.get("routing_index", {}).get("emotions", {})
        self.missing = set(raw.get("missing_emotions", []))
        if "upset" not in self.missing:
            raise ValueError("registry must explicitly preserve upset -> neutral fallback until a true reference exists")

    def _first_reference(self, emotion: str) -> tuple[str, str]:
        group = self.routing.get(emotion, {})
        for role in ("primary", "secondary"):
            candidates = group.get(role, [])
            if candidates:
                ref_id = str(candidates[0].get("id", ""))
                if ref_id in self.references:
                    return ref_id, role
        for ref_id, item in self.references.items():
            if item.get("emotion") == emotion:
                return ref_id, str(item.get("role", "primary"))
        raise ValueError(f"no enabled reference for {emotion}")

    def route(self, requested: str) -> Route:
        normalized = requested.strip().lower() or "neutral"
        canonical = ALIASES.get(normalized, normalized)
        if canonical not in CANONICAL_EMOTIONS:
            canonical = "neutral"
        used = "neutral" if canonical in self.missing else canonical
        try:
            ref_id, role = self._first_reference(used)
        except ValueError:
            used = "neutral"
            ref_id, role = self._first_reference(used)
        item = self.references[ref_id]
        return Route(requested=normalized, canonical=canonical, used=used, fallback=used != canonical,
                     reference_id=ref_id, role=role, selection="primary_first", audio=Path(item["audio"]),
                     prompt_text=str(item["prompt_text"]), prompt_language=str(item["prompt_lang"]))

    def public_profiles(self) -> list[dict[str, object]]:
        return [{"id": emotion, "available": emotion not in self.missing,
                 **({"fallback": "neutral"} if emotion in self.missing else {})}
                for emotion in CANONICAL_EMOTIONS]
