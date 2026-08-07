from __future__ import annotations

import re

from .memory import MemoryStore
from .models import MemorySnippet


_CURRENT_MARKERS = (
    "刚才",
    "刚刚",
    "现在",
    "目前",
    "当前",
    "接下来",
    "下一步",
    "进度",
    "进行到",
    "刚解决",
    "刚修",
)
_PROJECT_WORK_MARKERS = (
    "测试",
    "项目",
    "代码",
    "修复",
    "解决",
    "同步",
    "部署",
    "版本",
    "回归",
    "openclaw",
    "yogurt",
    "发送",
    "协调",
)
_VERSIONED_PROJECT_RE = re.compile(
    r"(?:\bv\d+(?:\.\d+){0,3}\b|alpha[.\-_]?\d+|yogurt|openclaw|social\s*layer|"
    r"统一发送|发送协调|回复规划|记忆召回|会话状态)",
    re.IGNORECASE,
)


def is_current_project_progress_query(message: str) -> bool:
    """Return true when immediate conversation state should beat old milestones."""

    compact = re.sub(r"\s+", "", message.casefold())
    return any(marker in compact for marker in _CURRENT_MARKERS) and any(
        marker in compact for marker in _PROJECT_WORK_MARKERS
    )


def is_versioned_project_episode(snippet: MemorySnippet) -> bool:
    return snippet.kind == "episode" and bool(_VERSIONED_PROJECT_RE.search(snippet.text))


class ContinuityAwareMemoryStore(MemoryStore):
    """V1 memory retrieval with a short-term continuity guard.

    Historical project milestones remain in SQLite and are still available for
    explicit history questions. For present-progress questions ("刚才解决了什么",
    "接下来测试什么"), versioned technical episodes are not injected as current
    facts. The active V4 conversation state / recent chat should answer those.
    """

    def relevant(self, user_id: str, message: str, limit: int = 3) -> list[MemorySnippet]:
        requested = max(0, limit)
        if requested == 0:
            return []

        # Fetch a wider pool so filtering stale episodes does not hide otherwise
        # useful stable facts/preferences.
        candidates = super().relevant(
            user_id=user_id,
            message=message,
            limit=max(12, requested * 4),
        )
        if is_current_project_progress_query(message):
            candidates = [
                item for item in candidates if not is_versioned_project_episode(item)
            ]
        return candidates[:requested]
