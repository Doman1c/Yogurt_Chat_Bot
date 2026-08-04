from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Optional

from .conversation_state import ConversationState


@dataclass(frozen=True)
class ResponsePlan:
    plan_version: str
    dialogue_act: str
    primary_goal: str
    tone: tuple[str, ...]
    reply_length: str
    ask_followup: bool
    bubble_target: int
    sticker_preference: str
    avoid: tuple[str, ...]
    continuity_topic: Optional[str]
    active_thread: Optional[str]
    state_version: int
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_version": self.plan_version,
            "dialogue_act": self.dialogue_act,
            "primary_goal": self.primary_goal,
            "tone": list(self.tone),
            "reply_length": self.reply_length,
            "ask_followup": self.ask_followup,
            "bubble_target": self.bubble_target,
            "sticker_preference": self.sticker_preference,
            "avoid": list(self.avoid),
            "continuity_topic": self.continuity_topic,
            "active_thread": self.active_thread,
            "state_version": self.state_version,
            "reasons": list(self.reasons),
        }


class ResponsePlanner:
    """Deterministic V4.1 planner using only bounded structured state.

    The planner does not call a model and never stores or returns the raw user
    message. The message is used only for bounded rule checks during this call.
    """

    PLAN_VERSION = "4.1"
    MAX_CONTEXT_CHARS = 1800

    _GOAL_TEXT = {
        "answer_question": "直接回答用户当前问题",
        "advance_current_project": "推进当前项目的下一步",
        "continue_current_thread": "延续当前话题并接着完成待办",
        "diagnose_and_resolve": "定位问题并给出可执行的排查或修复步骤",
        "request_missing_detail": "只询问解决问题所必需的一项信息",
        "acknowledge_achievement": "认可并庆祝用户刚完成的成果",
        "validate_emotion": "先回应用户感受，再提供适量支持",
        "acknowledge_without_repeating": "简短确认，不重新解释已经明确的内容",
        "respond_to_latest_message": "围绕用户最新一句话自然回应",
    }

    _TONE_TEXT = {
        "natural": "自然",
        "concise": "简洁",
        "collaborative": "协作",
        "technical": "技术清晰",
        "warm": "温柔",
        "supportive": "支持",
        "celebratory": "开心庆祝",
        "cute_subtle": "轻度可爱，不影响信息",
        "calm": "冷静",
        "direct": "直接",
    }

    _LENGTH_TEXT = {
        "very_short": "很短",
        "short": "简短",
        "medium": "中等",
        "detailed": "详细但避免重复背景",
    }

    _STICKER_TEXT = {
        "none": "不建议",
        "neutral": "中性，由表情系统独立判断",
        "celebration": "庆祝倾向",
        "comfort": "安慰倾向",
        "playful": "轻松倾向",
    }

    _AVOID_TEXT = {
        "repeat_background": "不要重新介绍已经明确的项目背景",
        "repeat_long_summary": "不要重复上一轮的长篇总结",
        "generic_followup_offer": "不要用“需要我继续吗”之类的机械式收尾",
        "unnecessary_question": "不要在任务已经明确时额外追问",
        "unsolicited_advice": "不要添加用户没有要求的建议",
        "overplayful": "不要用过度玩笑冲淡用户当前感受",
        "overcelebrate": "不要把普通进展夸张成庆祝场景",
        "excessive_roleplay": "技术内容中只保留轻微角色语气",
        "repeat_acknowledgement": "不要连续使用相同的确认句式",
    }

    @staticmethod
    def _compact(text: str) -> str:
        return re.sub(r"\s+", "", text.casefold())

    @staticmethod
    def _contains_any(text: str, markers: Iterable[str]) -> bool:
        return any(marker in text for marker in markers)

    @classmethod
    def _completion_signal(cls, message: str) -> bool:
        text = cls._compact(message)
        return cls._contains_any(
            text,
            (
                "已经完成",
                "已经实现",
                "实现完成",
                "测试通过",
                "全部通过",
                "成功合并",
                "搞定了",
                "完成了",
                "终于好了",
            ),
        )

    @classmethod
    def _needs_clarification(cls, message: str, state: ConversationState) -> bool:
        text = cls._compact(message)
        if len(text) > 18:
            return False
        if state.current_topic and state.current_topic != "日常对话":
            return False
        return cls._contains_any(text, ("这个", "那个", "它", "这里", "刚才那个"))

    @classmethod
    def _effective_act(cls, message: str, state: ConversationState) -> str:
        if cls._completion_signal(message):
            return "celebrate"
        if cls._needs_clarification(message, state):
            return "clarify"
        return state.dialogue_act

    @staticmethod
    def _append_unique(values: list[str], *items: str) -> None:
        for item in items:
            if item and item not in values:
                values.append(item)

    def plan(self, message: str, state: ConversationState) -> ResponsePlan:
        if not isinstance(message, str):
            raise TypeError("message must be a string")

        act = self._effective_act(message, state)
        tones: list[str] = ["natural"]
        avoid: list[str] = ["generic_followup_offer"]
        reasons: list[str] = [f"dialogue_act:{act}"]
        goal = "respond_to_latest_message"
        reply_length = "medium"
        ask_followup = False
        bubble_target = 2
        sticker_preference = "neutral"

        if act == "troubleshoot":
            goal = "diagnose_and_resolve"
            self._append_unique(tones, "technical", "collaborative", "direct")
            self._append_unique(avoid, "excessive_roleplay", "unnecessary_question")
            reply_length = "detailed"
            bubble_target = 1
            reasons.append("technical_resolution")
        elif act == "plan":
            goal = "advance_current_project"
            self._append_unique(tones, "collaborative", "technical", "direct")
            self._append_unique(
                avoid,
                "repeat_background",
                "unnecessary_question",
                "excessive_roleplay",
            )
            reply_length = "medium"
            bubble_target = 2
            reasons.append("project_planning")
        elif act == "continue":
            goal = "continue_current_thread"
            self._append_unique(tones, "collaborative", "direct")
            self._append_unique(avoid, "repeat_background", "unnecessary_question")
            reply_length = "medium"
            bubble_target = 2
            reasons.append("conversation_continuity")
        elif act == "celebrate":
            goal = "acknowledge_achievement"
            self._append_unique(tones, "warm", "celebratory", "cute_subtle")
            self._append_unique(avoid, "unsolicited_advice", "unnecessary_question")
            reply_length = "short"
            bubble_target = 2
            sticker_preference = "celebration"
            reasons.append("completion_or_celebration")
        elif act == "comfort":
            goal = "validate_emotion"
            self._append_unique(tones, "warm", "supportive", "calm")
            self._append_unique(
                avoid,
                "overplayful",
                "overcelebrate",
                "unsolicited_advice",
                "unnecessary_question",
            )
            reply_length = "short"
            bubble_target = 2
            sticker_preference = "comfort"
            reasons.append("emotion_first")
        elif act == "acknowledge":
            goal = "acknowledge_without_repeating"
            self._append_unique(tones, "concise", "warm")
            self._append_unique(
                avoid,
                "repeat_background",
                "repeat_acknowledgement",
                "unnecessary_question",
            )
            reply_length = "very_short"
            bubble_target = 1
            sticker_preference = "none"
            reasons.append("minimal_acknowledgement")
        elif act == "clarify":
            if self._needs_clarification(message, state):
                goal = "request_missing_detail"
                ask_followup = True
                reply_length = "very_short"
                bubble_target = 1
                self._append_unique(tones, "concise", "direct")
                reasons.append("missing_reference")
            else:
                goal = "answer_question"
                self._append_unique(tones, "direct")
                reply_length = "medium"
                bubble_target = 2
                reasons.append("question_answering")
        else:
            goal = "respond_to_latest_message"
            if state.interaction_mode == "technical":
                self._append_unique(tones, "technical", "direct")
                self._append_unique(avoid, "excessive_roleplay")
            elif state.interaction_mode == "collaborative":
                self._append_unique(tones, "collaborative")
            else:
                self._append_unique(tones, "warm", "cute_subtle")
            reasons.append("general_response")

        if state.user_mood in {"frustrated", "sad", "tired"}:
            self._append_unique(tones, "calm", "supportive")
            self._append_unique(avoid, "overplayful", "overcelebrate")
            if state.user_mood in {"sad", "tired"} and act not in {"troubleshoot", "plan"}:
                goal = "validate_emotion"
                reply_length = "short"
                sticker_preference = "comfort"
            reasons.append(f"mood:{state.user_mood}")

        if state.state_version > 1 and state.current_topic not in {None, "日常对话"}:
            self._append_unique(avoid, "repeat_background")
            reasons.append("existing_topic")

        if state.last_response_pattern in {
            "technical_long",
            "explanatory_medium",
            "multi_paragraph",
        }:
            self._append_unique(avoid, "repeat_long_summary")
            if act in {"continue", "acknowledge", "plan"}:
                reply_length = "short" if act != "plan" else "medium"
            reasons.append(f"previous_pattern:{state.last_response_pattern}")

        if state.interaction_mode == "technical":
            self._append_unique(avoid, "excessive_roleplay")

        active_thread = state.open_threads[-1] if state.open_threads else None
        return ResponsePlan(
            plan_version=self.PLAN_VERSION,
            dialogue_act=act,
            primary_goal=goal,
            tone=tuple(tones[:5]),
            reply_length=reply_length,
            ask_followup=ask_followup,
            bubble_target=max(1, min(3, bubble_target)),
            sticker_preference=sticker_preference,
            avoid=tuple(avoid[:8]),
            continuity_topic=state.current_topic,
            active_thread=active_thread,
            state_version=state.state_version,
            reasons=tuple(reasons[:8]),
        )

    def build_context_block(self, plan: ResponsePlan) -> str:
        goal = self._GOAL_TEXT.get(plan.primary_goal, "围绕用户最新一句话回应")
        tones = "、".join(self._TONE_TEXT.get(item, item) for item in plan.tone)
        length = self._LENGTH_TEXT.get(plan.reply_length, plan.reply_length)
        sticker = self._STICKER_TEXT.get(plan.sticker_preference, "中性")
        lines = [
            "[Yogurt Response Plan V4.1]",
            f"本轮行为：{plan.dialogue_act}",
            f"首要目标：{goal}",
            f"语气：{tones}",
            f"建议长度：{length}",
            f"建议气泡数：{plan.bubble_target}",
            f"主动追问：{'仅询问一项必要信息' if plan.ask_followup else '否'}",
            f"表情倾向：{sticker}",
        ]
        if plan.continuity_topic:
            lines.append(f"连续话题：{plan.continuity_topic}")
        if plan.active_thread:
            lines.append(f"当前待办：{plan.active_thread}")
        if plan.avoid:
            lines.append("避免：")
            lines.extend(
                f"- {self._AVOID_TEXT.get(item, item)}" for item in plan.avoid
            )
        lines.extend(
            [
                "执行规则：",
                "- 该计划只约束表达方式，不得覆盖用户最新消息的事实或要求。",
                "- 不要在回复中提及 Response Plan、状态字段、评分或内部流程。",
                "- 气泡数和表情仅为倾向，最终发送仍由本地发送层决定。",
            ]
        )
        context = "\n".join(lines)
        return context[: self.MAX_CONTEXT_CHARS]
