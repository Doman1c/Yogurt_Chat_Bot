from __future__ import annotations

import re
from collections.abc import Iterable

from .models import DialogueState, Mood, Relationship, ReplyLength, Scene


class StyleRouter:
    """Rule-based V1 router.

    It intentionally avoids an extra model call. The goal is deterministic,
    fast scene selection that can later be replaced or augmented by a model.
    """

    _SIGNALS: dict[Scene, tuple[str, ...]] = {
        Scene.COMFORT: (
            "难过", "伤心", "委屈", "孤独", "崩溃", "想哭", "不开心",
            "分手", "不理我", "不搭理我", "很累", "撑不住", "焦虑",
        ),
        Scene.CELEBRATION: (
            "成功了", "搞定了", "通过了", "完成了", "解决了", "录取了",
            "赢了", "太好了", "终于好了", "连接成功", "运行成功",
        ),
        Scene.TECHNICAL: (
            "报错", "错误", "代码", "脚本", "终端", "命令", "配置", "接口",
            "模型", "部署", "安装", "github", "openclaw", "ollama", "python",
            "json", "api", "token", "端口", "数据库", "怎么实现", "怎么修复",
        ),
        Scene.SERIOUS: (
            "签证", "行政审理", "合同", "法律", "投诉", "医院", "手术",
            "拔牙", "药", "剂量", "安全", "隐私", "账号被盗",
        ),
        Scene.PLAYFUL: (
            "哈哈", "嘿嘿", "笑死", "逗你", "开玩笑", "好可爱", "卖萌",
            "表情包", "喵", "哼", "笨蛋",
        ),
    }

    _QUESTION_PATTERNS = ("?", "？", "怎么", "为什么", "如何", "能不能", "可以吗", "是什么")
    _FRUSTRATION_SIGNALS = ("不行", "还是不行", "失败", "卡住", "卡在", "没反应", "连不上", "找不到")
    _ANXIETY_SIGNALS = ("担心", "害怕", "紧张", "会不会", "怎么办")

    def __init__(self, default_relationship: Relationship = Relationship.OWNER) -> None:
        self.default_relationship = default_relationship

    def analyze(self, message: str, relationship: str | Relationship | None = None) -> DialogueState:
        normalized = self._normalize(message)
        scene, matched = self._detect_scene(normalized)
        mood = self._detect_mood(normalized, scene)
        resolved_relationship = self._relationship(relationship)

        style, length, humor, use_memory, sticker, rules = self._policy(scene, mood, normalized)
        return DialogueState(
            scene=scene,
            mood=mood,
            relationship=resolved_relationship,
            reply_style=style,
            reply_length=length,
            humor_level=humor,
            use_memory=use_memory,
            use_sticker=sticker,
            response_rules=rules,
            matched_signals=matched,
        )

    @classmethod
    def _normalize(cls, message: str) -> str:
        return re.sub(r"\s+", " ", message.strip().lower())

    @classmethod
    def _contains_any(cls, text: str, signals: Iterable[str]) -> list[str]:
        return [signal for signal in signals if signal in text]

    def _detect_scene(self, text: str) -> tuple[Scene, list[str]]:
        # Emotional distress takes priority over technical keywords. This keeps
        # the latest emotional intent from being buried by incidental details.
        priority = (
            Scene.COMFORT,
            Scene.CELEBRATION,
            Scene.SERIOUS,
            Scene.TECHNICAL,
            Scene.PLAYFUL,
        )
        for scene in priority:
            matched = self._contains_any(text, self._SIGNALS[scene])
            if matched:
                return scene, matched

        if re.search(r"```|traceback|\b(error|exception|config|curl|git)\b", text):
            return Scene.TECHNICAL, ["technical_pattern"]
        return Scene.CASUAL, []

    def _detect_mood(self, text: str, scene: Scene) -> Mood:
        if scene is Scene.COMFORT:
            return Mood.SAD
        if scene is Scene.CELEBRATION:
            return Mood.HAPPY
        if self._contains_any(text, self._FRUSTRATION_SIGNALS):
            return Mood.FRUSTRATED
        if self._contains_any(text, self._ANXIETY_SIGNALS):
            return Mood.ANXIOUS
        if self._contains_any(text, self._QUESTION_PATTERNS):
            return Mood.CURIOUS
        return Mood.NEUTRAL

    def _relationship(self, relationship: str | Relationship | None) -> Relationship:
        if isinstance(relationship, Relationship):
            return relationship
        if isinstance(relationship, str):
            try:
                return Relationship(relationship)
            except ValueError:
                pass
        return self.default_relationship

    @staticmethod
    def _policy(
        scene: Scene,
        mood: Mood,
        message: str,
    ) -> tuple[str, ReplyLength, float, bool, bool, list[str]]:
        if scene is Scene.COMFORT:
            return (
                "warm_and_present",
                ReplyLength.SHORT,
                0.0,
                True,
                False,
                [
                    "先回应用户具体说的事情和情绪",
                    "不要立即分析、说教或给解决方案",
                    "除非用户主动询问，否则不要把话题转向睡觉、工作或旧记忆",
                    "语气自然、真诚，避免模板化安慰",
                ],
            )
        if scene is Scene.TECHNICAL:
            detailed = len(message) >= 160 or "详细" in message or "步骤" in message
            return (
                "precise_and_collaborative",
                ReplyLength.DETAILED if detailed else ReplyLength.MEDIUM,
                0.05,
                True,
                False,
                [
                    "先明确当前状态或错误原因",
                    "给出可直接执行的步骤或命令",
                    "不要用人设语气干扰技术准确性",
                    "不确定的地方要明确标注，不编造结果",
                ],
            )
        if scene is Scene.CELEBRATION:
            return (
                "warm_and_upbeat",
                ReplyLength.SHORT,
                0.25,
                True,
                True,
                [
                    "先和用户一起庆祝已经完成的事情",
                    "可以轻微表现酸奶的开心，但不要夸张卖萌",
                    "不要立刻用新的任务淹没庆祝时刻",
                ],
            )
        if scene is Scene.SERIOUS:
            return (
                "calm_and_careful",
                ReplyLength.MEDIUM,
                0.0,
                True,
                False,
                [
                    "保持冷静、准确和边界清晰",
                    "先回答用户最关心的问题",
                    "涉及风险时说明不确定性和安全边界",
                ],
            )
        if scene is Scene.PLAYFUL:
            return (
                "lightly_playful",
                ReplyLength.SHORT,
                0.45,
                True,
                True,
                [
                    "可以轻微调皮或使用一次喵",
                    "不要连续使用口头禅或过度卖萌",
                    "仍然要回应用户实际表达的内容",
                ],
            )
        return (
            "natural_and_warm",
            ReplyLength.SHORT,
            0.15,
            True,
            False,
            [
                "直接回应用户最新一句话",
                "日常聊天保持简短自然",
                "不要未经询问主动给出一串建议",
            ],
        )
