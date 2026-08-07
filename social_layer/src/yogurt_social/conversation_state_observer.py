from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping, Optional

from .conversation_state import ConversationState
from .conversation_state_runtime import ConversationStateRuntimeStore
from .memory_recall_gate import MemoryRecallGate
from .project_state import ProjectStateStore, project_key_from_status
from .project_state_parser import parse_project_state_update
from .project_state_resolver import resolve_project_state
from .response_planner import ResponsePlanner


_CONTINUITY_CONTEXT_MAX_CHARS = 1750
_PROJECT_STATUS_MAX_ITEMS = 5
_PROJECT_STATUS_MAX_ITEM_CHARS = 220
_DEFAULT_PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _request(stream: Any) -> Mapping[str, Any]:
    raw = stream.read()
    if not raw:
        raise ValueError("expected one JSON request on stdin")
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("request must be a JSON object")
    return payload


def _bounded_text(value: Any, limit: int) -> str:
    return value.strip()[:limit] if isinstance(value, str) else ""


def _load_project_status(project_root: Optional[Path]) -> dict[str, Any] | None:
    """Load a tiny versioned repository baseline; failure is always fail-open."""

    if project_root is None:
        return None
    path = project_root / "social_layer" / "config" / "project_status.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(raw, dict) or raw.get("schema_version") != 1:
        return None

    completed = raw.get("completed") if isinstance(raw.get("completed"), list) else []
    next_items = raw.get("next") if isinstance(raw.get("next"), list) else []
    return {
        "project": _bounded_text(raw.get("project"), 80),
        "current_version": _bounded_text(raw.get("current_version"), 40),
        "plugin_version": _bounded_text(raw.get("plugin_version"), 40),
        "current_stage": _bounded_text(raw.get("current_stage"), 180),
        "current_topic": _bounded_text(raw.get("current_topic"), 80),
        "completed": [
            _bounded_text(item, _PROJECT_STATUS_MAX_ITEM_CHARS)
            for item in completed[:_PROJECT_STATUS_MAX_ITEMS]
            if _bounded_text(item, _PROJECT_STATUS_MAX_ITEM_CHARS)
        ],
        "next": [
            _bounded_text(item, _PROJECT_STATUS_MAX_ITEM_CHARS)
            for item in next_items[:_PROJECT_STATUS_MAX_ITEMS]
            if _bounded_text(item, _PROJECT_STATUS_MAX_ITEM_CHARS)
        ],
        "historical_versions_are_background_only": (
            raw.get("historical_versions_are_background_only") is True
        ),
    }


def _project_topic_matches(
    state: ConversationState,
    repository_status: Mapping[str, Any] | None,
) -> bool:
    if repository_status is None:
        return state.current_topic not in {None, "日常对话"}
    status_topic = _bounded_text(repository_status.get("current_topic"), 80)
    return bool(
        not status_topic
        or not state.current_topic
        or status_topic == state.current_topic
    )


def _with_current_continuity_context(
    base_context: str,
    state: ConversationState,
    project_status: Mapping[str, Any] | None = None,
    *,
    project_state_updated: bool = False,
) -> str:
    """Put resolved current project/session state ahead of old milestone memories."""

    base_lines = base_context.splitlines()
    if not base_lines:
        return base_context

    continuity = ["[Yogurt Current Continuity]"]
    if state.current_topic:
        continuity.append(f"当前阶段：{state.current_topic}")

    if project_status is not None:
        version = _bounded_text(project_status.get("current_version"), 48)
        plugin_version = _bounded_text(project_status.get("plugin_version"), 48)
        stage = _bounded_text(project_status.get("current_stage"), 180)
        source = _bounded_text(project_status.get("source"), 64)
        state_version = project_status.get("state_version")
        if version:
            continuity.append(f"当前项目版本：{version}")
        if plugin_version:
            continuity.append(f"当前插件版本：{plugin_version}")
        if stage:
            continuity.append(f"当前工程阶段：{stage}")
        if source.startswith("user_explicit"):
            continuity.append("项目状态来源：用户明确确认（最高优先级）")
        else:
            continuity.append("项目状态来源：仓库 Project Status 基线")
        if isinstance(state_version, int):
            continuity.append(f"ProjectState 版本：{state_version}")

        completed = project_status.get("completed")
        if isinstance(completed, list) and completed:
            continuity.append("当前已确认进展：")
            continuity.extend(f"- {item}" for item in completed[-4:] if isinstance(item, str))
        next_items = project_status.get("next")
        if isinstance(next_items, list) and next_items:
            continuity.append("当前下一步：")
            continuity.extend(f"- {item}" for item in next_items[:4] if isinstance(item, str))
        if project_state_updated:
            continuity.append(
                "本轮用户明确更新了项目进度，ProjectState 已写入；回复时自然简短确认已记住本次更新。"
            )

    if state.open_threads:
        continuity.append(f"当前会话待办：{state.open_threads[-1]}")
    if state.recent_actions:
        continuity.append("当前会话最近已确认进展：")
        continuity.extend(f"- {item}" for item in state.recent_actions[-3:])
    continuity.extend(
        [
            "连续性规则：",
            "- 用户明确确认的 ProjectState 优先级最高；仓库 Project Status 是代码基线；当前会话状态用于补充本轮上下文。",
            "- 这里描述的是当前最新项目状态，优先于长期记忆中的旧项目版本或旧阶段里程碑。",
            "- 如果长期记忆提到 V1、V4.0 等已被当前阶段取代的版本，只能作为历史背景，绝不能说成“刚刚完成”或当前进度。",
            "- 回答“刚才/现在/接下来/进行到哪”时，只能依据当前 ProjectState、仓库基线、当前会话状态和最近对话；信息不足时不要自行回退到旧版本里程碑。",
        ]
    )
    combined = "\n".join([base_lines[0], *continuity, *base_lines[1:]])
    return combined[:_CONTINUITY_CONTEXT_MAX_CHARS]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Yogurt V4 conversation-state observer, V4.1 response planner, "
            "V4.2 shadow memory-recall gate, and persistent ProjectState continuity"
        )
    )
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--project-root", type=Path, default=_DEFAULT_PROJECT_ROOT)
    parser.add_argument("--topic-ttl-minutes", type=int, default=60)
    parser.add_argument("--mood-ttl-minutes", type=int, default=30)
    parser.add_argument("--thread-ttl-minutes", type=int, default=1440)
    parser.add_argument("--max-open-threads", type=int, default=5)
    parser.add_argument("--max-recent-actions", type=int, default=6)
    parser.add_argument("--memory-recall-min-score", type=float, default=0.48)
    parser.add_argument("--memory-recall-cooldown-turns", type=int, default=8)
    parser.add_argument(
        "--memory-recall-same-memory-cooldown-turns",
        type=int,
        default=20,
    )
    parser.add_argument("--memory-recall-max-candidates", type=int, default=5)
    parser.add_argument("--pretty", action="store_true")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        request = _request(sys.stdin)
        user_id = request.get("user_id")
        conversation_key = request.get("conversation_key")
        phase = request.get("phase")
        message = request.get("message", "")
        assistant_reply = request.get("assistant_reply", "")
        include_response_plan = request.get("include_response_plan") is True
        include_memory_recall = request.get("include_memory_recall") is True
        if not isinstance(user_id, str) or not user_id.strip():
            raise ValueError("user_id must be a non-empty string")
        if not isinstance(conversation_key, str) or not conversation_key.strip():
            raise ValueError("conversation_key must be a non-empty string")
        if phase not in {"user", "assistant"}:
            raise ValueError("phase must be user or assistant")
        if not isinstance(message, str) or not isinstance(assistant_reply, str):
            raise ValueError("message fields must be strings")
        if include_response_plan and phase != "user":
            raise ValueError("response plan is only available for user phase")
        if include_memory_recall and phase != "user":
            raise ValueError("memory recall is only available for user phase")

        store = ConversationStateRuntimeStore(
            args.db,
            topic_ttl_minutes=args.topic_ttl_minutes,
            mood_ttl_minutes=args.mood_ttl_minutes,
            thread_ttl_minutes=args.thread_ttl_minutes,
            max_open_threads=args.max_open_threads,
            max_recent_actions=args.max_recent_actions,
        )
        project_store = ProjectStateStore(args.db)
        repository_status = _load_project_status(args.project_root)
        project_key = project_key_from_status(repository_status)

        response_plan = None
        response_plan_context = ""
        memory_recall = None
        project_state_update = None
        resolved_project_state = None

        if phase == "user":
            state = store.observe_user_turn(user_id, conversation_key, message)
            active_project = _project_topic_matches(state, repository_status)
            parsed_update = parse_project_state_update(
                message,
                active_project=active_project,
            )
            explicit_state = project_store.get(user_id, project_key)
            if parsed_update is not None:
                explicit_state = project_store.update(
                    user_id,
                    project_key,
                    parsed_update.patch,
                    source=parsed_update.source,
                )
                project_state_update = {
                    "updated": True,
                    "source": parsed_update.source,
                    "correction": parsed_update.correction,
                    "state_version": explicit_state.state_version,
                }

            resolved_project_state = resolve_project_state(
                project_key=project_key,
                repository_status=repository_status,
                explicit_state=explicit_state,
            )
            use_project_context = active_project or parsed_update is not None

            if include_response_plan:
                planner = ResponsePlanner()
                response_plan = planner.plan(message, state)
                response_plan_context = _with_current_continuity_context(
                    planner.build_context_block(response_plan),
                    state,
                    (
                        resolved_project_state.to_dict()
                        if resolved_project_state is not None and use_project_context
                        else None
                    ),
                    project_state_updated=parsed_update is not None,
                )
            if include_memory_recall:
                recall_gate = MemoryRecallGate(
                    args.db,
                    min_score=args.memory_recall_min_score,
                    cooldown_turns=args.memory_recall_cooldown_turns,
                    same_memory_cooldown_turns=(
                        args.memory_recall_same_memory_cooldown_turns
                    ),
                    max_candidates=args.memory_recall_max_candidates,
                )
                memory_recall = recall_gate.evaluate(
                    user_id=user_id,
                    conversation_key=conversation_key,
                    message=message,
                    state=state,
                )
        else:
            state = store.observe_assistant_turn(
                user_id,
                conversation_key,
                assistant_reply,
            )
            explicit_state = project_store.get(user_id, project_key)
            resolved_project_state = resolve_project_state(
                project_key=project_key,
                repository_status=repository_status,
                explicit_state=explicit_state,
            )

        modes = []
        if response_plan is not None:
            modes.append("response_planner")
        if memory_recall is not None:
            modes.append("memory_recall_shadow")
        if project_state_update is not None:
            modes.append("project_state_updated")
        result = {
            "ok": True,
            "mode": "+".join(modes) if modes else "observe_only",
            "phase": phase,
            "state": state.to_dict(),
            "project_state": (
                resolved_project_state.to_dict()
                if resolved_project_state is not None
                else None
            ),
            "project_state_update": project_state_update,
            "response_plan": response_plan.to_dict() if response_plan is not None else None,
            "response_plan_context": response_plan_context,
            "memory_recall": (
                memory_recall.to_dict(include_text=False)
                if memory_recall is not None
                else None
            ),
        }
    except Exception as exc:
        result = {
            "ok": False,
            "mode": "observe_only",
            "error": "conversation_state_error",
            "error_type": type(exc).__name__,
        }
    print(json.dumps(result, ensure_ascii=False, indent=2 if args.pretty else None))
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
