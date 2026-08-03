from __future__ import annotations

import json
import re
import sys
from typing import Any, Iterable, Optional

from .auto_memory import (
    AutoMemoryStore,
    DeterministicMemoryExtractor,
    MemoryCandidate,
    _read_request,
    build_parser,
)


_ACADEMIC_PATTERN = re.compile(
    r"(?:目前|现在)?我(?:目前|现在)?在"
    r"(?P<school>[^，。！？!?]{2,40}?)"
    r"(?:攻读|就读|读)"
    r"(?P<major>[^，。！？!?]{2,50}?)"
    r"(?:的)?"
    r"(?P<degree>m\.?s\.?|master(?:'s)?|硕士|ph\.?d\.?|博士|本科)"
    r"(?:学位|项目|专业)?(?=[，,。！？!?]|$)",
    re.IGNORECASE,
)

_CURRENT_LOCATION_PATTERN = re.compile(
    r"(?:^|[，,。！？!?])"
    r"(?:我)?(?:现在|目前)?"
    r"(?:回到?|到了?|人在|待在)"
    r"(?P<location>[^，,。！？!?]{1,30}?)"
    r"(?:了|啦)?(?=[，,。！？!?]|$)"
)


class ExtendedDeterministicMemoryExtractor(DeterministicMemoryExtractor):
    """Extract only the patterns added after real-device V3 feedback.

    The base automatic-memory runtime continues to handle its established
    patterns. This incremental extractor therefore must not return base
    candidates, otherwise one chat turn would be counted twice.
    """

    def extract(self, user_message: str) -> list[MemoryCandidate]:
        extra: list[MemoryCandidate] = []
        extra.extend(self._assistant_behavior(user_message))
        extra.extend(self._academic_facts(user_message))
        extra.extend(self._temporary_context(user_message))

        result: list[MemoryCandidate] = []
        seen: set[tuple[str, str, str, int | None]] = set()
        for raw in extra:
            candidate = raw.normalized()
            signature = (
                candidate.kind,
                candidate.key,
                candidate.value.casefold(),
                candidate.polarity,
            )
            if not candidate.value or signature in seen:
                continue
            seen.add(signature)
            result.append(candidate)
        return result[:8]

    @staticmethod
    def _assistant_behavior(text: str) -> Iterable[MemoryCandidate]:
        sticker_over_emoji = re.search(
            r"(?:请|尽量|以后|平时)?(?:多|优先)?(?:用|发)"
            r"(?P<preferred>表情包|emoji|表情)"
            r"(?:而不是|不要|少用|不用)"
            r"(?P<avoided>emoji|表情包|表情)",
            text,
            re.IGNORECASE,
        )
        if sticker_over_emoji:
            preferred = sticker_over_emoji.group("preferred")
            avoided = sticker_over_emoji.group("avoided")
            yield MemoryCandidate(
                kind="preference",
                key="assistant_behavior",
                value=f"优先使用{preferred}而不是{avoided}",
                importance=0.94,
                confidence=0.94,
                reason="assistant_media_preference",
                polarity=1,
                update_signal=True,
            )

        bubble = re.search(
            r"(?:适合的?时候|合适时|平时|以后)?"
            r"(?:把|将)回复分成"
            r"(?P<count>[^，,。！？!?]{1,20}?)"
            r"(?:个)?(?:短)?气泡",
            text,
        )
        if bubble:
            count = bubble.group("count").strip()
            value = f"适合时将回复分成{count}个短气泡".replace("个个", "个")
            yield MemoryCandidate(
                kind="preference",
                key="assistant_behavior",
                value=value,
                importance=0.92,
                confidence=0.92,
                reason="assistant_multi_bubble_preference",
                polarity=1,
                update_signal=True,
            )

    @staticmethod
    def _academic_facts(text: str) -> Iterable[MemoryCandidate]:
        match = _ACADEMIC_PATTERN.search(text)
        if not match:
            return

        school = match.group("school").strip()
        major = match.group("major").strip().removesuffix("的")
        raw_degree = match.group("degree").strip()
        normalized_degree = re.sub(r"[.]", "", raw_degree).upper()
        degree = {
            "MASTER'S": "硕士",
            "MASTER": "硕士",
            "硕士": "硕士",
            "PHD": "PhD",
            "博士": "博士",
            "本科": "本科",
            "MS": "MS",
        }.get(normalized_degree, raw_degree)

        yield MemoryCandidate(
            kind="fact",
            key="school",
            value=school,
            importance=0.90,
            confidence=0.92,
            reason="academic_school",
        )
        yield MemoryCandidate(
            kind="fact",
            key="major",
            value=major,
            importance=0.90,
            confidence=0.92,
            reason="academic_major",
        )
        yield MemoryCandidate(
            kind="fact",
            key="degree",
            value=degree,
            importance=0.86,
            confidence=0.90,
            reason="academic_degree",
        )

    @staticmethod
    def _temporary_context(text: str) -> Iterable[MemoryCandidate]:
        match = _CURRENT_LOCATION_PATTERN.search(text)
        if not match:
            return
        location = match.group("location").strip()
        if not location:
            return
        yield MemoryCandidate(
            kind="fact",
            key="current_location",
            value=location,
            importance=0.64,
            confidence=0.86,
            reason="temporary_current_location",
            update_signal=True,
        )


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        request = _read_request(sys.stdin)
        user_id = request.get("user_id")
        user_message = request.get("user_message")
        assistant_reply = request.get("assistant_reply", "")
        if not isinstance(user_id, str) or not user_id.strip():
            raise ValueError("user_id must be a non-empty string")
        if not isinstance(user_message, str) or not isinstance(assistant_reply, str):
            raise ValueError("message fields must be strings")

        store = AutoMemoryStore(args.db, promotion_threshold=args.promotion_threshold)
        results = store.process_chat(
            user_id=user_id.strip(),
            user_message=user_message,
            assistant_reply=assistant_reply,
            extractor=ExtendedDeterministicMemoryExtractor(),
        )
        payload: dict[str, Any] = {
            "ok": True,
            "candidate_count": len(results),
            "promoted_count": sum(item.status == "promoted" for item in results),
            "pending_count": sum(item.status == "pending" for item in results),
            "conflict_count": sum(item.status == "conflict" for item in results),
            "results": [item.to_dict() for item in results],
        }
    except Exception as exc:
        payload = {"ok": False, "error": type(exc).__name__}

    print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None))
    return 0 if payload.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
