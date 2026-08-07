from __future__ import annotations

from yogurt_social.project_state import ProjectStatePatch, ProjectStateStore
from yogurt_social.project_state_parser import parse_project_state_update
from yogurt_social.project_state_resolver import resolve_project_state


def test_explicit_progress_parser_extracts_current_state():
    parsed = parse_project_state_update(
        "目前进度到 V4.3.2 alpha.6，本地测试已经通过，现在在做 Shadow 实机验证，接下来测试快速双 Turn",
        active_project=True,
    )
    assert parsed is not None
    assert parsed.source == "user_explicit"
    assert parsed.patch.current_version == "V4.3.2"
    assert parsed.patch.plugin_version == "alpha.6"
    assert parsed.patch.current_stage == "Shadow 实机验证"
    assert "本地测试已经通过" in parsed.patch.completed_add
    assert parsed.patch.next_items == ("测试快速双 Turn",)
    assert parsed.patch.replace_next is True


def test_hypothetical_future_version_does_not_update():
    assert (
        parse_project_state_update(
            "以后 V4.4 可以做 WebUI，我觉得下一版可能叫 V5",
            active_project=True,
        )
        is None
    )
    assert (
        parse_project_state_update(
            "如果 alpha.7 做这个会不会更好",
            active_project=True,
        )
        is None
    )


def test_store_persists_updates_and_revisions(tmp_path):
    store = ProjectStateStore(tmp_path / "state.sqlite3")
    first = store.update(
        "owner",
        "yogurt-chat-bot",
        ProjectStatePatch(
            current_version="V4.3.2",
            plugin_version="alpha.6",
            current_stage="Shadow 实机验证",
            completed_add=("本地回归通过",),
            next_items=("快速双 Turn", "Sticker 交错"),
            replace_next=True,
        ),
    )
    assert first.state_version == 1
    assert first.current_version == "V4.3.2"
    assert first.next_items == ("快速双 Turn", "Sticker 交错")

    second = store.update(
        "owner",
        "yogurt-chat-bot",
        ProjectStatePatch(
            completed_add=("Shadow 实机通过",),
            next_items=("快速双 Turn",),
            replace_next=True,
        ),
    )
    assert second.state_version == 2
    assert second.current_version == "V4.3.2"
    assert second.plugin_version == "alpha.6"
    assert second.current_stage == "Shadow 实机验证"
    assert second.completed == ("本地回归通过", "Shadow 实机通过")
    assert second.next_items == ("快速双 Turn",)

    revisions = store.revisions("owner", "yogurt-chat-bot")
    assert [item["state_version"] for item in revisions] == [2, 1]
    assert revisions[0]["source"] == "user_explicit"
    assert "completed" in revisions[0]["changed_fields"]


def test_correction_replaces_only_explicit_field(tmp_path):
    store = ProjectStateStore(tmp_path / "state.sqlite3")
    store.update(
        "owner",
        "yogurt-chat-bot",
        ProjectStatePatch(
            current_version="V4.3.2",
            plugin_version="alpha.7",
            current_stage="Enforced sanity",
            completed_add=("Shadow 通过",),
        ),
    )
    parsed = parse_project_state_update(
        "不对，现在其实还是 alpha.6，不是 alpha.7",
        active_project=True,
    )
    assert parsed is not None
    assert parsed.source == "user_explicit_correction"
    assert parsed.patch.plugin_version == "alpha.6"

    corrected = store.update(
        "owner",
        "yogurt-chat-bot",
        parsed.patch,
        source=parsed.source,
    )
    assert corrected.plugin_version == "alpha.6"
    assert corrected.current_version == "V4.3.2"
    assert corrected.current_stage == "Enforced sanity"
    assert corrected.completed == ("Shadow 通过",)


def test_resolver_prefers_user_state_over_repository_baseline(tmp_path):
    store = ProjectStateStore(tmp_path / "state.sqlite3")
    explicit = store.update(
        "owner",
        "yogurt-chat-bot",
        ProjectStatePatch(
            current_version="V4.4",
            current_stage="ProjectState WebUI API",
            next_items=("WebUI 读取 ProjectState",),
            replace_next=True,
        ),
    )
    resolved = resolve_project_state(
        project_key="yogurt-chat-bot",
        repository_status={
            "project": "Yogurt Chat Bot",
            "current_version": "V4.3.2",
            "plugin_version": "0.6.3-alpha.6",
            "current_stage": "Shadow validation",
            "completed": ["alpha.6 CI passed"],
            "next": ["rapid two-turn test"],
        },
        explicit_state=explicit,
    )
    assert resolved is not None
    assert resolved.current_version == "V4.4"
    assert resolved.plugin_version == "0.6.3-alpha.6"
    assert resolved.current_stage == "ProjectState WebUI API"
    assert resolved.next_items == ("WebUI 读取 ProjectState",)
    assert resolved.field_sources["current_version"] == "user_explicit"
    assert resolved.field_sources["plugin_version"] == "repository_snapshot"
