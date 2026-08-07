from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .project_state import ProjectState


_MAX_ITEMS = 8
_MAX_ITEM_CHARS = 220


def _text(value: Any, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = " ".join(value.strip().split())
    return normalized[:limit] if normalized else None


def _items(value: Any, limit: int = _MAX_ITEMS) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    result: list[str] = []
    for item in value:
        text = _text(item, _MAX_ITEM_CHARS)
        if text and text not in result:
            result.append(text)
        if len(result) >= limit:
            break
    return tuple(result)


def _merge_items(*groups: tuple[str, ...], limit: int = _MAX_ITEMS) -> tuple[str, ...]:
    result: list[str] = []
    for group in groups:
        for item in group:
            if item not in result:
                result.append(item)
    return tuple(result[-limit:])


@dataclass(frozen=True)
class ResolvedProjectState:
    project: str
    project_key: str
    current_topic: str | None
    current_version: str | None
    plugin_version: str | None
    current_stage: str | None
    completed: tuple[str, ...]
    next_items: tuple[str, ...]
    state_version: int | None
    source: str
    field_sources: Mapping[str, str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "project": self.project,
            "project_key": self.project_key,
            "current_topic": self.current_topic,
            "current_version": self.current_version,
            "plugin_version": self.plugin_version,
            "current_stage": self.current_stage,
            "completed": list(self.completed),
            "next": list(self.next_items),
            "state_version": self.state_version,
            "source": self.source,
            "field_sources": dict(self.field_sources),
        }


def resolve_project_state(
    *,
    project_key: str,
    repository_status: Mapping[str, Any] | None,
    explicit_state: ProjectState | None,
) -> ResolvedProjectState | None:
    """Resolve current project status with explicit user state above repository baseline."""

    if repository_status is None and explicit_state is None:
        return None

    repository_status = repository_status or {}
    project = _text(repository_status.get("project"), 80) or "Yogurt Chat Bot"
    current_topic = _text(repository_status.get("current_topic"), 80)
    repo_version = _text(repository_status.get("current_version"), 48)
    repo_plugin = _text(repository_status.get("plugin_version"), 48)
    repo_stage = _text(repository_status.get("current_stage"), 180)
    repo_completed = _items(repository_status.get("completed"))
    repo_next = _items(repository_status.get("next"))

    field_sources: dict[str, str] = {}
    current_version = explicit_state.current_version if explicit_state else None
    if current_version:
        field_sources["current_version"] = explicit_state.source
    else:
        current_version = repo_version
        if current_version:
            field_sources["current_version"] = "repository_snapshot"

    plugin_version = explicit_state.plugin_version if explicit_state else None
    if plugin_version:
        field_sources["plugin_version"] = explicit_state.source
    else:
        plugin_version = repo_plugin
        if plugin_version:
            field_sources["plugin_version"] = "repository_snapshot"

    current_stage = explicit_state.current_stage if explicit_state else None
    if current_stage:
        field_sources["current_stage"] = explicit_state.source
    else:
        current_stage = repo_stage
        if current_stage:
            field_sources["current_stage"] = "repository_snapshot"

    explicit_completed = explicit_state.completed if explicit_state else ()
    completed = _merge_items(repo_completed, explicit_completed)
    if explicit_completed:
        field_sources["completed"] = explicit_state.source
    elif repo_completed:
        field_sources["completed"] = "repository_snapshot"

    explicit_next = explicit_state.next_items if explicit_state else ()
    next_items = explicit_next if explicit_next else repo_next
    if explicit_next:
        field_sources["next"] = explicit_state.source
    elif repo_next:
        field_sources["next"] = "repository_snapshot"

    source = explicit_state.source if explicit_state else "repository_snapshot"
    return ResolvedProjectState(
        project=project,
        project_key=project_key,
        current_topic=current_topic,
        current_version=current_version,
        plugin_version=plugin_version,
        current_stage=current_stage,
        completed=completed,
        next_items=next_items,
        state_version=explicit_state.state_version if explicit_state else None,
        source=source,
        field_sources=field_sources,
    )
