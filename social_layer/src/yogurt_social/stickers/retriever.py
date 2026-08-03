from __future__ import annotations

import re
from collections.abc import Iterable

from .models import StickerCandidate, StickerCatalogSnapshot, StickerRecord


_COMMAND_TERMS = (
    "酸奶", "给我", "帮我", "请", "发一个", "发个", "发张", "来一个", "来个", "来张",
    "整一个", "整张", "甩一个", "丢一个", "送一个", "看看", "看下", "用一个",
    "表情包", "表情", "贴图", "动图", "图片",
)
_GENERIC_TERMS = {
    "", "一个", "一张", "随便", "随机", "任意", "都行", "都可以", "什么都行", "来点",
}
_EMOTION_ALIASES: dict[str, tuple[str, ...]] = {
    "happy": ("开心", "高兴", "快乐", "笑", "哈哈", "好耶"),
    "sad": ("难过", "伤心", "想哭", "委屈", "不开心"),
    "speechless": ("无语", "离谱", "不知道说什么", "说不出话", "人傻了", "绷不住"),
    "embarrassed": ("尴尬", "社死", "不好意思", "丢人", "强颜欢笑"),
    "affectionate": ("想你", "喜欢", "爱你", "抱抱", "贴贴", "亲亲"),
    "angry": ("生气", "气死", "火大", "愤怒", "烦死", "讨厌"),
    "surprised": ("震惊", "惊了", "居然", "竟然", "没想到", "真的假的"),
    "confused": ("疑惑", "迷惑", "看不懂", "不明白", "懵了", "什么意思"),
    "tired": ("累", "好累", "困", "好困", "没精神", "累瘫"),
    "proud": ("骄傲", "厉害", "做到了", "赢了", "成功"),
    "warm": ("温柔", "暖心", "谢谢", "辛苦", "陪陪", "安慰"),
    "anxious": ("焦虑", "担心", "害怕", "紧张", "怎么办"),
    "frustrated": ("烦", "失败", "卡住", "不行", "没反应"),
    "curious": ("好奇", "为什么", "怎么", "是什么"),
    "neutral": (),
}
_TONE_ALIASES: dict[str, tuple[str, ...]] = {
    "cute": ("可爱", "卖萌", "萌", "喵"),
    "playful": ("调皮", "逗", "吐槽", "开玩笑", "笨蛋"),
    "warm": ("温柔", "安慰", "抱抱", "摸头", "陪伴"),
    "sarcastic": ("阴阳", "嘲讽", "吐槽", "无语"),
    "serious": ("认真", "严肃", "警告"),
}


def _normalize(text: str) -> str:
    return re.sub(r"\s+", "", text.casefold())


def _strip_request_terms(message: str) -> str:
    value = _normalize(message)
    for term in sorted(_COMMAND_TERMS, key=len, reverse=True):
        value = value.replace(term, "")
    value = re.sub(r"[，。！？!?、,.~～:：;；\-—_]+", "", value)
    return value.strip()


def _contains_any(text: str, values: Iterable[str]) -> list[str]:
    return [value for value in values if value and _normalize(value) in text]


def _inferred_labels(text: str, aliases: dict[str, tuple[str, ...]]) -> list[str]:
    labels: list[str] = []
    for label, terms in aliases.items():
        if _contains_any(text, terms):
            labels.append(label)
    return labels


class StickerRetriever:
    """V2.1 semantic-first deterministic sticker ranker.

    Specific language and emotion outrank broad scene labels. ``weight`` is a
    bounded prior and can no longer multiply a generic sticker into first place.
    """

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
        assistant_reply: str = "",
    ) -> list[StickerCandidate]:
        if not allow_sticker and not explicit_request:
            return []

        user_text = _normalize(message)
        assistant_text = _normalize(assistant_reply)
        combined = f"{user_text}{assistant_text}"
        specific_query = _strip_request_terms(message) if explicit_request else user_text
        explicit_mode = self.explicit_mode(message) if explicit_request else "proactive"
        eligible = [
            sticker
            for sticker in self.snapshot.stickers
            if self._is_eligible(sticker, relationship=relationship, combined=combined)
        ]

        candidates: list[StickerCandidate] = []
        for sticker in eligible:
            candidate = self._score(
                sticker,
                user_text=user_text,
                assistant_text=assistant_text,
                specific_query=specific_query,
                scene=scene,
                mood=mood,
                relationship=relationship,
                explicit_mode=explicit_mode,
            )
            if candidate is not None:
                candidates.append(candidate)

        candidates.sort(key=lambda item: (-item.score, item.sticker_id))
        if explicit_mode == "explicit_specific":
            candidates = [item for item in candidates if item.score >= self.minimum_score]
        elif explicit_mode == "explicit_generic" and candidates:
            top = candidates[0]
            if top.score < self.minimum_score:
                candidates[0] = StickerCandidate(
                    sticker_id=top.sticker_id,
                    file=top.file,
                    description=top.description,
                    score=self.minimum_score,
                    scenes=top.scenes,
                    emotions=top.emotions,
                    tones=top.tones,
                    matched_signals=(*top.matched_signals, "intent:explicit_generic"),
                    score_breakdown=(
                        *top.score_breakdown,
                        ("explicit_generic_floor", self.minimum_score - top.score),
                    ),
                )
        return candidates[: self.max_candidates]

    @staticmethod
    def explicit_mode(message: str) -> str:
        query = _strip_request_terms(message)
        return "explicit_generic" if query in _GENERIC_TERMS or len(query) <= 1 else "explicit_specific"

    @staticmethod
    def _is_eligible(
        sticker: StickerRecord,
        *,
        relationship: str,
        combined: str,
    ) -> bool:
        if sticker.status != "approved" or not sticker.enabled:
            return False
        if sticker.content_warnings != ("none",):
            return False
        if sticker.relationships and relationship not in sticker.relationships:
            return False
        if any(_normalize(value) in combined for value in sticker.avoid_when if value):
            return False
        return True

    def _score(
        self,
        sticker: StickerRecord,
        *,
        user_text: str,
        assistant_text: str,
        specific_query: str,
        scene: str,
        mood: str,
        relationship: str,
        explicit_mode: str,
    ) -> StickerCandidate | None:
        combined = f"{user_text}{assistant_text}"
        score = 0.0
        matched: list[str] = []
        breakdown: list[tuple[str, float]] = []

        def add(name: str, value: float, signal: str | None = None) -> None:
            nonlocal score
            if value == 0:
                return
            score += value
            breakdown.append((name, round(value, 4)))
            if signal:
                matched.append(signal)

        exact_user_keywords = [
            keyword for keyword in sticker.keywords if _normalize(keyword) in user_text
        ][:2]
        for keyword in exact_user_keywords:
            add("keyword_user", 5.0, f"keyword:user:{keyword}")

        exact_reply_keywords = [
            keyword
            for keyword in sticker.keywords
            if keyword not in exact_user_keywords and _normalize(keyword) in assistant_text
        ][:2]
        for keyword in exact_reply_keywords:
            add("keyword_reply", 2.5, f"keyword:reply:{keyword}")

        inferred_emotions = set(_inferred_labels(combined, _EMOTION_ALIASES))
        if mood and mood != "neutral":
            inferred_emotions.add(mood)
        emotion_matches = [emotion for emotion in sticker.emotions if emotion in inferred_emotions]
        for emotion in emotion_matches[:2]:
            add("emotion", 4.0, f"emotion:{emotion}")

        query_terms = [
            term
            for term in dict.fromkeys([specific_query, *exact_user_keywords, *exact_reply_keywords])
            if len(term) >= 2
        ]
        normalized_description = _normalize(sticker.description)
        normalized_ocr = _normalize(sticker.ocr_text)
        description_hits = [term for term in query_terms if term in normalized_description][:1]
        ocr_hits = [term for term in query_terms if term in normalized_ocr][:1]
        for term in description_hits:
            add("description", 2.5, f"description:{term}")
        for term in ocr_hits:
            add("ocr", 1.5, f"ocr:{term}")

        inferred_tones = set(_inferred_labels(combined, _TONE_ALIASES))
        for tone in [tone for tone in sticker.tones if tone in inferred_tones][:1]:
            add("tone", 1.0, f"tone:{tone}")

        if scene in sticker.scenes:
            add("scene", 1.0, f"scene:{scene}")
        if relationship in sticker.relationships:
            add("relationship", 0.5, f"relationship:{relationship}")

        weight_prior = max(-0.5, min(0.5, sticker.weight - 1.0))
        add("weight_prior", weight_prior, f"weight:{sticker.weight:.2f}")
        confidence_prior = max(-0.25, min(0.25, (sticker.confidence - 0.75) * 0.5))
        add("confidence_prior", confidence_prior)

        if explicit_mode == "explicit_specific" and specific_query:
            if any(
                specific_query in _normalize(value)
                for value in (
                    *sticker.keywords,
                    *sticker.emotions,
                    *sticker.tones,
                    sticker.description,
                    sticker.ocr_text,
                )
            ):
                add("specific_query", 1.0, "intent:explicit_specific_match")

        rounded = round(score, 4)
        if rounded <= 0:
            return None
        return StickerCandidate(
            sticker_id=sticker.id,
            file=sticker.file,
            description=sticker.description,
            score=rounded,
            scenes=sticker.scenes,
            emotions=sticker.emotions,
            tones=sticker.tones,
            matched_signals=tuple(matched),
            score_breakdown=tuple(breakdown),
        )
