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
        explicit_request: bool = False,
    ) -> list[StickerCandidate]:
        if not allow_sticker and not explicit_request:
            return []

        normalized = message.casefold()
        eligible = [
            sticker
            for sticker in self.snapshot.stickers
            if self._is_eligible(sticker, relationship=relationship)
        ]
        candidates: list[StickerCandidate] = []
        for sticker in eligible:
            candidate = self._score(
                sticker,
                normalized=normalized,
                scene=scene,
                mood=mood,
                relationship=relationship,
                explicit_request=explicit_request,
            )
            if candidate is not None:
                candidates.append(candidate)

        candidates.sort(key=lambda item: (-item.score, item.sticker_id))
        if candidates or not explicit_request:
            return candidates[: self.max_candidates]

        # A direct user request is stronger than the ordinary proactive threshold.
        # Keep every safety/approval/relationship filter, but do not silently fail
        # just because reviewed metadata, weights, or exact Chinese keywords leave
        # all otherwise-safe stickers slightly below minimum_score.
        fallback = self._explicit_fallback(
            eligible,
            normalized=normalized,
            scene=scene,
            mood=mood,
            relationship=relationship,
        )
        return [fallback] if fallback is not None else []

    @staticmethod
    def _is_eligible(sticker: StickerRecord, *, relationship: str) -> bool:
        if sticker.status != "approved" or not sticker.enabled:
            return False
        if sticker.content_warnings != ("none",):
            return False
        if sticker.relationships and relationship not in sticker.relationships:
            return False
        return True

    def _score(
        self,
        sticker: StickerRecord,
        *,
        normalized: str,
        scene: str,
        mood: str,
        relationship: str,
        explicit_request: bool,
    ) -> StickerCandidate | None:
        score, matched = self._score_signals(
            sticker,
            normalized=normalized,
            scene=scene,
            mood=mood,
            relationship=relationship,
        )
        if explicit_request:
            # Explicit intent is deliberately strong enough to recover reviewed
            # stickers whose weight is below 1.0 while preserving ranking.
            score += 3.0
            matched.append("intent:explicit_sticker_request")

        weighted_score = round(score * sticker.weight, 4)
        if weighted_score < self.minimum_score:
            return None
        return self._candidate(sticker, weighted_score, matched)

    def _explicit_fallback(
        self,
        stickers: list[StickerRecord],
        *,
        normalized: str,
        scene: str,
        mood: str,
        relationship: str,
    ) -> StickerCandidate | None:
        ranked: list[tuple[float, float, str, StickerRecord, list[str]]] = []
        for sticker in stickers:
            score, matched = self._score_signals(
                sticker,
                normalized=normalized,
                scene=scene,
                mood=mood,
                relationship=relationship,
            )
            relevance = score * sticker.weight
            ranked.append(
                (
                    relevance,
                    sticker.weight,
                    sticker.id,
                    sticker,
                    [*matched, "intent:explicit_fallback"],
                )
            )

        if not ranked:
            return None
        ranked.sort(key=lambda item: (-item[0], -item[1], item[2]))
        relevance, _weight, _sticker_id, sticker, matched = ranked[0]
        return self._candidate(
            sticker,
            round(max(self.minimum_score, relevance), 4),
            matched,
        )

    @staticmethod
    def _score_signals(
        sticker: StickerRecord,
        *,
        normalized: str,
        scene: str,
        mood: str,
        relationship: str,
    ) -> tuple[float, list[str]]:
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
        return score, matched

    @staticmethod
    def _candidate(
        sticker: StickerRecord,
        score: float,
        matched: list[str],
    ) -> StickerCandidate:
        return StickerCandidate(
            sticker_id=sticker.id,
            file=sticker.file,
            description=sticker.description,
            score=score,
            scenes=sticker.scenes,
            emotions=sticker.emotions,
            tones=sticker.tones,
            matched_signals=tuple(matched),
        )
