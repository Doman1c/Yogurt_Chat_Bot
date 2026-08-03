from __future__ import annotations

import re

from .models import StickerCandidate, StickerSendDecision
from .retriever import StickerRetriever


_HIGH_RISK_COMFORT = (
    "不想活", "想死", "自杀", "伤害自己", "割腕", "跳楼", "急救", "报警",
    "大量出血", "呼吸困难", "昏迷", "中毒",
)
_TECHNICAL_MARKERS = (
    "```", "traceback", "exception", "error", "curl ", "git ", "python ",
    "json", "端口", "配置文件", "报错", "命令",
)
_SERIOUS_MARKERS = (
    "签证", "法律", "合同", "剂量", "手术", "行政审理", "隐私", "账号被盗",
)
_POSITIVE_INTENTS: dict[str, tuple[str, ...]] = {
    "celebration": ("成功", "搞定", "完成", "通过", "赢了", "太好了", "好耶"),
    "playful": ("哈哈", "笑死", "笨蛋", "逗你", "离谱", "无语", "绷不住"),
    "comfort": ("难过", "伤心", "委屈", "想哭", "焦虑", "很累", "好累", "好困"),
    "affection": ("想你", "喜欢你", "爱你", "抱抱", "贴贴", "亲亲"),
    "greeting": ("早上好", "早安", "晚安", "我回来", "回来啦", "到家了"),
    "surprise": ("震惊", "居然", "竟然", "没想到", "真的假的"),
}


def _normalized(value: str) -> str:
    return re.sub(r"\s+", "", value.casefold())


class StickerSendDecider:
    """Independent V2.1 send decision.

    This class answers only whether a sticker is appropriate. Candidate ranking
    remains the retriever's responsibility; cooldown and rate limits remain in
    the TypeScript delivery policy.
    """

    def decide(
        self,
        *,
        user_message: str,
        assistant_reply: str,
        scene: str,
        mood: str,
        explicit_request: bool,
        candidates: list[StickerCandidate],
        phase: str = "post_reply",
        forbidden: bool = False,
    ) -> StickerSendDecision:
        explicit_mode = (
            StickerRetriever.explicit_mode(user_message)
            if explicit_request
            else "proactive"
        )
        user = _normalized(user_message)
        reply = _normalized(assistant_reply)
        combined = f"{user}{reply}"
        if forbidden:
            return StickerSendDecision(False, "forbidden", 0.0, 999.0, (), ("user_forbid",))
        if any(marker in combined for marker in _HIGH_RISK_COMFORT):
            return StickerSendDecision(
                False, "safety_block", 0.0, 999.0, (), ("high_risk_comfort",)
            )
        if not candidates:
            return StickerSendDecision(False, explicit_mode, 0.0, 0.0, (), ("no_candidates",))
        if explicit_request:
            top = candidates[0]
            threshold = 5.0
            accepted = explicit_mode == "explicit_generic" or top.score >= threshold
            return StickerSendDecision(
                accepted,
                explicit_mode,
                top.score,
                threshold,
                tuple(top.emotions[:2]),
                (
                    "explicit_request",
                    "specific_match" if explicit_mode == "explicit_specific" else "generic_request",
                ),
            )
        if phase != "post_reply":
            return StickerSendDecision(
                False,
                "proactive",
                0.0,
                0.0,
                (),
                ("awaiting_assistant_reply",),
                deferred=True,
            )

        if scene in {"technical", "serious"}:
            return StickerSendDecision(
                False, "proactive", 0.0, 999.0, (), (f"blocked_scene:{scene}",)
            )

        score = 0.0
        reasons: list[str] = []
        intents: list[str] = []
        scene_base = {
            "celebration": 3.0,
            "playful": 2.5,
            "comfort": 2.2,
            "casual": 0.8,
        }.get(scene, 0.0)
        score += scene_base
        reasons.append(f"scene:{scene}:{scene_base:.1f}")

        for intent, markers in _POSITIVE_INTENTS.items():
            user_hit = any(marker in user for marker in markers)
            reply_hit = any(marker in reply for marker in markers)
            if not user_hit and not reply_hit:
                continue
            intents.append(intent)
            contribution = (1.8 if user_hit else 0.0) + (1.2 if reply_hit else 0.0)
            score += contribution
            reasons.append(f"intent:{intent}:{contribution:.1f}")

        if mood not in {"", "neutral", "curious"}:
            score += 0.8
            intents.append(mood)
            reasons.append(f"mood:{mood}:0.8")

        top = candidates[0]
        runner_up = candidates[1] if len(candidates) > 1 else None
        candidate_quality = min(2.5, top.score / 4.0)
        score += candidate_quality
        reasons.append(f"candidate_quality:{candidate_quality:.2f}")
        margin = top.score - runner_up.score if runner_up else top.score
        margin_bonus = min(1.5, max(0.0, margin) / 2.0)
        score += margin_bonus
        reasons.append(f"candidate_margin:{margin_bonus:.2f}")

        if any(marker in reply for marker in _TECHNICAL_MARKERS):
            score -= 4.0
            reasons.append("technical_reply:-4.0")
        if any(marker in combined for marker in _SERIOUS_MARKERS):
            score -= 3.0
            reasons.append("serious_topic:-3.0")
        if len(assistant_reply) >= 220:
            score -= 1.5
            reasons.append("long_reply:-1.5")
        elif len(assistant_reply) <= 45 and assistant_reply.strip():
            score += 0.5
            reasons.append("short_emotive_reply:+0.5")

        threshold = {
            "celebration": 5.0,
            "playful": 5.5,
            "comfort": 6.2,
            "casual": 6.5,
        }.get(scene, 7.0)
        rounded = round(score, 4)
        return StickerSendDecision(
            rounded >= threshold,
            "proactive",
            rounded,
            threshold,
            tuple(dict.fromkeys(intents)),
            tuple(reasons),
        )
