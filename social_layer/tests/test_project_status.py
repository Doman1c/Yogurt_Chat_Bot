from __future__ import annotations

import json
from dataclasses import replace

from yogurt_social.conversation_state_observer import (
    _load_project_status,
    _with_current_continuity_context,
)
from yogurt_social.conversation_state_runtime import ConversationStateRuntimeStore
from yogurt_social.response_planner import ResponsePlanner


def _state(tmp_path):
    store = ConversationStateRuntimeStore(tmp_path / "state.sqlite3")
    key = "session:project-status"
    store.observe_user_turn(
        "owner",
        key,
        "开始 V4.3.2 统一发送协调器 alpha.6 实机 Shadow 验证",
    )
    return store.observe_user_turn(
        "owner",
        key,
        "刚才解决了什么，接下来要测试什么",
    )


def test_project_status_is_injected_ahead_of_historical_progress(tmp_path):
    root = tmp_path / "repo"
    config = root / "social_layer" / "config"
    config.mkdir(parents=True)
    (config / "project_status.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "project": "Yogurt Chat Bot",
                "current_version": "V4.3.2",
                "plugin_version": "0.6.3-alpha.6",
                "current_stage": "real-device Shadow validation",
                "current_topic": "统一发送协调",
                "completed": ["alpha.6 local regression passed"],
                "next": ["verify matched=true on the real WeChat bot"],
                "historical_versions_are_background_only": True,
            }
        ),
        encoding="utf-8",
    )

    status = _load_project_status(root)
    assert status is not None
    state = _state(tmp_path)
    planner = ResponsePlanner()
    context = _with_current_continuity_context(
        planner.build_context_block(planner.plan("刚才解决了什么，接下来要测试什么", state)),
        state,
        status,
    )

    assert "权威项目版本：V4.3.2" in context
    assert "当前插件版本：0.6.3-alpha.6" in context
    assert "当前工程阶段：real-device Shadow validation" in context
    assert "alpha.6 local regression passed" in context
    assert "verify matched=true on the real WeChat bot" in context
    assert "仓库 Project Status 是工程进度的权威来源" in context
    assert context.index("权威项目版本：V4.3.2") < context.index("连续性规则：")


def test_project_status_does_not_leak_into_unrelated_topic(tmp_path):
    state = replace(_state(tmp_path), current_topic="日常对话")
    planner = ResponsePlanner()
    context = _with_current_continuity_context(
        planner.build_context_block(planner.plan("今天天气怎么样", state)),
        state,
        {
            "current_topic": "统一发送协调",
            "current_version": "V4.3.2",
            "plugin_version": "0.6.3-alpha.6",
            "current_stage": "real-device Shadow validation",
            "completed": [],
            "next": [],
        },
    )
    assert "权威项目版本：V4.3.2" not in context


def test_invalid_project_status_fails_open(tmp_path):
    root = tmp_path / "repo"
    config = root / "social_layer" / "config"
    config.mkdir(parents=True)
    (config / "project_status.json").write_text("not-json", encoding="utf-8")
    assert _load_project_status(root) is None
