from __future__ import annotations

import io
import json
import sys

from yogurt_social.conversation_state_observer import main


def _write_repository_status(root):
    config = root / "social_layer" / "config"
    config.mkdir(parents=True)
    (config / "project_status.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "project": "Yogurt Chat Bot",
                "current_version": "V4.3.2",
                "plugin_version": "0.6.3-alpha.6",
                "current_stage": "repository Shadow baseline",
                "current_topic": "统一发送协调",
                "completed": ["repository alpha.6 CI passed"],
                "next": ["repository rapid two-turn test"],
                "historical_versions_are_background_only": True,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _run_observer(monkeypatch, capsys, db_path, project_root, payload):
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload, ensure_ascii=False)))
    code = main(
        [
            "--db",
            str(db_path),
            "--project-root",
            str(project_root),
        ]
    )
    assert code == 0
    return json.loads(capsys.readouterr().out)


def test_user_explicit_progress_persists_and_beats_repository(monkeypatch, capsys, tmp_path):
    project_root = tmp_path / "repo"
    _write_repository_status(project_root)
    db_path = tmp_path / "state.sqlite3"
    key = "session:project-explicit"

    first = _run_observer(
        monkeypatch,
        capsys,
        db_path,
        project_root,
        {
            "user_id": "owner",
            "conversation_key": key,
            "phase": "user",
            "message": (
                "目前进度到 V4.3.3 alpha.7，本地 ProjectState 测试已经通过，"
                "现在在做 SQLite 持久化验证，接下来测试微信里直接更新进度"
            ),
            "include_response_plan": True,
        },
    )

    assert first["project_state_update"]["updated"] is True
    assert first["project_state_update"]["source"] == "user_explicit"
    assert first["project_state"]["current_version"] == "V4.3.3"
    assert first["project_state"]["plugin_version"] == "alpha.7"
    assert first["project_state"]["current_stage"] == "SQLite 持久化验证"
    assert first["project_state"]["next"] == ["测试微信里直接更新进度"]
    assert "当前项目版本：V4.3.3" in first["response_plan_context"]
    assert "当前插件版本：alpha.7" in first["response_plan_context"]
    assert "项目状态来源：用户明确确认（最高优先级）" in first["response_plan_context"]
    assert "ProjectState 已写入" in first["response_plan_context"]

    second = _run_observer(
        monkeypatch,
        capsys,
        db_path,
        project_root,
        {
            "user_id": "owner",
            "conversation_key": key,
            "phase": "user",
            "message": "那我们现在做到哪里了，接下来是什么？",
            "include_response_plan": True,
        },
    )
    assert second["project_state_update"] is None
    assert second["project_state"]["current_version"] == "V4.3.3"
    assert second["project_state"]["plugin_version"] == "alpha.7"
    assert "当前项目版本：V4.3.3" in second["response_plan_context"]
    assert "repository Shadow baseline" not in second["response_plan_context"]


def test_hypothetical_version_does_not_write_project_state(monkeypatch, capsys, tmp_path):
    project_root = tmp_path / "repo"
    _write_repository_status(project_root)
    payload = _run_observer(
        monkeypatch,
        capsys,
        tmp_path / "state.sqlite3",
        project_root,
        {
            "user_id": "owner",
            "conversation_key": "session:hypothetical",
            "phase": "user",
            "message": "以后 V4.4 可以做 WebUI，我觉得 alpha.8 也许可以加这个",
            "include_response_plan": True,
        },
    )
    assert payload["project_state_update"] is None
    assert payload["project_state"]["current_version"] == "V4.3.2"
    assert payload["project_state"]["plugin_version"] == "0.6.3-alpha.6"
