from __future__ import annotations

from .models import StickerCandidate, StickerCatalogSnapshot, StickerRecord


class StickerRetriever:
    def __init__(
        self,
        snapshot: StickerCatalogSnapshot,
        minimum_score: float = 5.0,
        max_candidates: int = 5,
    ) -> None:
        self.snapshot = snapshot
        self.minimum_score = minimum_score
        self.max_candidates = max_candidates

    def retrieve(
        self,
        *,
        message: str,
        scene: str,
        mood: str,
        relationship: str,
        allow_sticker: bool,
    ) -> list[StickerCandidate]:
        if not allow_sticker:
            return []
        normalized = message.casefold()
        candidates: list[StickerCandidate] = []
        for sticker in self.snapshot.stickers:
            candidate = self._score(
                sticker,
                normalized=normalized,
                scene=scene,
                mood=mood,
                relationship=relationship,
            )
            if candidate is not None:
                candidates.append(candidate)
        candidates.sort(key=lambda item: (-item.score, item.sticker_id))
        return candidates[: self.max_candidates]

    def _score(
        self,
        sticker: StickerRecord,
        *,
        normalized: str,
        scene: str,
        mood: str,
        relationship: str,
    ) -> StickerCandidate | None:
        if sticker.status != "approved" or not sticker.enabled:
            return None
        if sticker.content_warnings != ("none",):
            return None
        if sticker.relationships and relationship not in sticker.relationships:
            return None

        score = 0.0
        matched: list[str] = []
        if scene in sticker.scenes:
            score += 4.0
            matched.append(f"scene:{scene}")
        if mood in sticker.emotions:
            score += 3.0
            matched.append(f"mood:{mood}")

        keyword_matches = [
            keyword for keyword in sticker.keywords if keyword.casefold() in normalized
        ][:2]
        if keyword_matches:
            score += 2.0 * len(keyword_matches)
            matched.extend(f"keyword:{keyword}" for keyword in keyword_matches)
        if relationship in sticker.relationships:
            score += 1.0
            matched.append(f"relationship:{relationship}")

        weighted_score = round(score * sticker.weight, 4)
        if weighted_score < self.minimum_score:
            return None
        return StickerCandidate(
            sticker_id=sticker.id,
            file=sticker.file,
            description=sticker.description,
            score=weighted_score,
            scenes=sticker.scenes,
            emotions=sticker.emotions,
            tones=sticker.tones,
            matched_signals=tuple(matched),
        )
