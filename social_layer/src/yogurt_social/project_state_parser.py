from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from .project_state import ProjectStatePatch


_CURRENT_VERSION_RE = re.compile(r"\bV\d+(?:\.\d+){1,3}\b", re.IGNORECASE)
_PLUGIN_VERSION_RE = re.compile(
    r"\b\d+\.\d+\.\d+-alpha[._-]?\d+\b|\balpha[._-]?\d+\b",
    re.IGNORECASE,
)
_EXPLICIT_PROGRESS_MARKERS = (
    "目前进度",
    "现在进度",
    "当前进度",
    "进度到",
    "现在做到",
    "目前做到",
    "当前做到",
    "现在已经到",
    "目前已经到",
    "当前版本",
    "现在版本",
    "把当前进度",
    "更新当前进度",
    "更新进度",
    "记一下进度",
    "记住现在",
    "我们现在是",
    "现在是v",
    "目前是v",
)
_PROGRESS_QUERY_MARKERS = (
    "做到哪里",
    "做到哪",
    "到哪里了",
    "到哪了",
    "什么进度",
    "进度怎么样",
    "现在是什么版本",
    "现在是哪个版本",
    "目前是什么版本",
    "接下来是什么",
    "下一步是什么",
    "进行到哪里",
    "进行到哪",
)
_CORRECTION_MARKERS = (
    "不对",
    "更正",
    "纠正",
    "改一下",
    "其实还是",
    "应该还是",
    "实际还是",
)
_HYPOTHETICAL_MARKERS = (
    "如果",
    "假如",
    "可能",
    "也许",
    "以后",
    "未来",
    "考虑",
    "打算",
    "想做",
    "可以做",
    "下一版可能",
    "maybe",
)
_PROJECT_MARKERS = (
    "v4",
    "v5",
    "alpha",
    "openclaw",
    "yogurt",
    "shadow",
    "enforced",
    "sticker",
    "turn",
    "项目",
    "版本",
    "测试",
    "回归",
    "实机",
    "发送",
    "协调",
    "插件",
)
_COMPLETION_MARKERS = (
    "已经完成",
    "完成了",
    "已经通过",
    "测试通过",
    "测试都通过",
    "全部通过",
    "回归通过",
    "验证通过",
    "验收通过",
    "搞定了",
    "做完了",
    "已修复",
    "修好了",
)
_NEXT_MARKERS = (
    "接下来",
    "下一步",
    "然后开始",
    "之后开始",
    "后面开始",
    "接着做",
    "继续做",
    "准备做",
    "准备测试",
    "准备验证",
)
_STAGE_MARKERS = (
    "当前阶段",
    "现在在做",
    "现在正在做",
    "现在做",
    "目前在做",
    "目前正在做",
    "目前做",
    "现在进行",
    "目前进行",
    "现在进入",
    "目前进入",
    "现在测试",
    "目前测试",
    "现在验证",
    "目前验证",
)


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", text.casefold())


def _contains_any(text: str, markers: Iterable[str]) -> bool:
    return any(marker in text for marker in markers)


def _clean_version(value: str | None) -> str | None:
    if not value:
        return None
    value = value.strip()
    if value.lower().startswith("v"):
        return "V" + value[1:]
    lowered = value.lower().replace("alpha_", "alpha.").replace("alpha-", "alpha.")
    return lowered


def _clauses(message: str) -> list[str]:
    return [
        " ".join(item.strip().split())
        for item in re.split(r"[，,。；;！？!?\n]+", message)
        if item.strip()
    ]


def _strip_prefix(clause: str, markers: Iterable[str]) -> str:
    result = clause.strip()
    compact = _compact(result)
    for marker in markers:
        if marker not in compact:
            continue
        match = re.search(re.escape(marker), result, re.IGNORECASE)
        if match:
            result = result[match.end() :]
        break
    result = re.sub(r"^[：:是为到在做进行进入测试验证要将把\s]+", "", result)
    return " ".join(result.split())[:180]


def _projectish(clause: str) -> bool:
    compact = _compact(clause)
    return _contains_any(compact, _PROJECT_MARKERS) or bool(
        _CURRENT_VERSION_RE.search(clause) or _PLUGIN_VERSION_RE.search(clause)
    )


def _completion_items(clauses: list[str], active_project: bool) -> tuple[str, ...]:
    items: list[str] = []
    for clause in clauses:
        compact = _compact(clause)
        if not _contains_any(compact, _COMPLETION_MARKERS):
            continue
        if not active_project and not _projectish(clause):
            continue
        text = clause.strip()[:180]
        if text and text not in items:
            items.append(text)
    return tuple(items[:5])


def _next_items(clauses: list[str], active_project: bool) -> tuple[str, ...]:
    items: list[str] = []
    for clause in clauses:
        compact = _compact(clause)
        if not _contains_any(compact, _NEXT_MARKERS):
            continue
        if _contains_any(compact, _PROGRESS_QUERY_MARKERS):
            continue
        if not active_project and not _projectish(clause):
            continue
        text = _strip_prefix(clause, _NEXT_MARKERS)
        if text and text not in items:
            items.append(text)
    return tuple(items[:5])


def _stage(clauses: list[str], active_project: bool) -> str | None:
    for clause in clauses:
        compact = _compact(clause)
        if not _contains_any(compact, _STAGE_MARKERS):
            continue
        if _contains_any(compact, _PROGRESS_QUERY_MARKERS):
            continue
        if not active_project and not _projectish(clause):
            continue
        stage = _strip_prefix(clause, _STAGE_MARKERS)
        if stage:
            return stage[:180]
    return None


@dataclass(frozen=True)
class ParsedProjectStateUpdate:
    patch: ProjectStatePatch
    source: str
    correction: bool


def parse_project_state_update(
    message: str,
    *,
    active_project: bool = False,
) -> ParsedProjectStateUpdate | None:
    """Parse only explicit/confirmed current-project updates.

    Questions about current progress are always read-only. Hypothetical future
    ideas are intentionally ignored. This parser never calls a model and returns
    only bounded structured fields, not the full chat turn.
    """

    if not isinstance(message, str) or not message.strip():
        return None
    compact = _compact(message)
    correction = _contains_any(compact, _CORRECTION_MARKERS)
    progress_query = _contains_any(compact, _PROGRESS_QUERY_MARKERS)
    if progress_query and not correction and not _contains_any(compact, _COMPLETION_MARKERS):
        return None

    explicit_progress = _contains_any(compact, _EXPLICIT_PROGRESS_MARKERS)
    clauses = _clauses(message)

    has_confirmed_action = any(
        _contains_any(_compact(clause), _COMPLETION_MARKERS + _NEXT_MARKERS + _STAGE_MARKERS)
        and not _contains_any(_compact(clause), _PROGRESS_QUERY_MARKERS)
        and (active_project or _projectish(clause))
        for clause in clauses
    )
    if not explicit_progress and not correction and not has_confirmed_action:
        return None

    if (
        _contains_any(compact, _HYPOTHETICAL_MARKERS)
        and not explicit_progress
        and not correction
        and not _contains_any(compact, _COMPLETION_MARKERS)
    ):
        return None

    current_match = _CURRENT_VERSION_RE.search(message)
    plugin_match = _PLUGIN_VERSION_RE.search(message)
    current_version = _clean_version(current_match.group(0) if current_match else None)
    plugin_version = _clean_version(plugin_match.group(0) if plugin_match else None)

    completed = _completion_items(clauses, active_project)
    next_items = _next_items(clauses, active_project)
    stage = _stage(clauses, active_project)

    if not explicit_progress and not correction:
        if current_version and not has_confirmed_action:
            current_version = None
        if plugin_version and not has_confirmed_action:
            plugin_version = None

    patch = ProjectStatePatch(
        current_version=current_version,
        plugin_version=plugin_version,
        current_stage=stage,
        completed_add=completed,
        next_items=next_items,
        replace_next=bool(next_items),
    )
    if patch.empty():
        return None
    return ParsedProjectStateUpdate(
        patch=patch,
        source="user_explicit_correction" if correction else "user_explicit",
        correction=correction,
    )
