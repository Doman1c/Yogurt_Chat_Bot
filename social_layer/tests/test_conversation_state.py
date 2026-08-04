import json
import sqlite3
from datetime import datetime, timedelta, timezone

from yogurt_social.conversation_state_runtime import ConversationStateRuntimeStore


def test_v4_plan_turn_creates_bounded_structured_state(tmp_path):
    store = ConversationStateRuntimeStore(tmp_path / "memory.sqlite3")
    message = "开始编写 V4 代码吧，然后我本地同步，再开始测试"
    state = store.observe_user_turn("owner", "session:v4", message)

    assert state.current_topic == "Yogurt V4 开发"
    assert state.dialogue_act == "plan"
    assert state.interaction_mode == "technical"
    assert state.user_mood == "neutral"
    assert state.open_threads == (
        "实现 V4.0 会话状态",
        "本地同步 V4 分支",
        "运行 V4.0 测试",
    )
    assert state.state_version == 1


def test_assistant_observation_updates_pattern_without_raw_reply(tmp_path):
    db_path = tmp_path / "memory.sqlite3"
    store = ConversationStateRuntimeStore(db_path)
    store.observe_user_turn("owner", "session:v4", "开始实现 V4 状态引擎")
    reply = "下面开始编写代码：\n\n```python\nprint('v4')\n```"
    state = store.observe_assistant_turn("owner", "session:v4", reply)

    assert state.last_response_pattern == "technical_code"
    assert state.state_version == 2

    with sqlite3.connect(db_path) as connection:
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(conversation_states)")
        }
        rows = connection.execute(
            "SELECT changed_fields_json FROM conversation_state_events"
        ).fetchall()
    assert "message" not in columns
    assert "assistant_reply" not in columns
    serialized_events = "\n".join(row[0] for row in rows)
    assert reply not in serialized_events


def test_state_isolated_by_user_and_conversation_key(tmp_path):
    store = ConversationStateRuntimeStore(tmp_path / "memory.sqlite3")
    store.observe_user_turn("owner", "session:a", "开始编写 V4 代码")
    store.observe_user_turn("friend", "session:a", "我今天有点难过")
    store.observe_user_turn("owner", "session:b", "微信机器人现在正常吗？")

    owner_a = store.get("owner", "session:a")
    friend_a = store.get("friend", "session:a")
    owner_b = store.get("owner", "session:b")

    assert owner_a is not None and owner_a.current_topic == "Yogurt V4 开发"
    assert friend_a is not None and friend_a.user_mood == "sad"
    assert owner_b is not None and owner_b.current_topic == "微信机器人"
    assert owner_a.conversation_key != owner_b.conversation_key


def test_topic_and_mood_expire_without_mutating_a_read(tmp_path):
    store = ConversationStateRuntimeStore(
        tmp_path / "memory.sqlite3",
        topic_ttl_minutes=60,
        mood_ttl_minutes=30,
        thread_ttl_minutes=120,
    )
    start = datetime(2026, 8, 4, 0, 0, tzinfo=timezone.utc)
    store.observe_user_turn(
        "owner",
        "session:v4",
        "开始开发 V4，我太开心了",
        now=start,
    )

    after_mood_ttl = store.get(
        "owner",
        "session:v4",
        now=start + timedelta(minutes=31),
    )
    assert after_mood_ttl is not None
    assert after_mood_ttl.current_topic == "Yogurt V4 开发"
    assert after_mood_ttl.user_mood == "neutral"

    after_topic_ttl = store.get(
        "owner",
        "session:v4",
        now=start + timedelta(minutes=61),
    )
    assert after_topic_ttl is not None
    assert after_topic_ttl.current_topic is None
    assert after_topic_ttl.open_threads

    raw = store.list_events("owner", "session:v4")
    assert len(raw) == 1, "read-only inspection must not create events"


def test_completed_threads_are_removed_and_actions_are_audited(tmp_path):
    store = ConversationStateRuntimeStore(tmp_path / "memory.sqlite3")
    store.observe_user_turn(
        "owner",
        "session:v4",
        "开始编写 V4 代码，然后本地同步，再运行测试",
    )
    completed = store.observe_user_turn(
        "owner",
        "session:v4",
        "V4.0 代码已经实现，测试通过了",
    )

    assert "实现 V4.0 会话状态" not in completed.open_threads
    assert "运行 V4.0 测试" not in completed.open_threads
    assert "本地同步 V4 分支" in completed.open_threads
    assert "V4.0 测试已通过" in completed.recent_actions
    assert "V4.0 状态引擎已实现" in completed.recent_actions

    events = store.list_events("owner", "session:v4")
    assert events[0]["event_type"] == "user_turn"
    assert "recent_actions" in events[0]["changed_fields"]


def test_inspector_payload_is_json_safe(tmp_path):
    store = ConversationStateRuntimeStore(tmp_path / "memory.sqlite3")
    store.observe_user_turn("owner", "session:v4", "开始开发 V4")
    payload = {
        "states": store.list_states("owner"),
        "events": store.list_events("owner", "session:v4"),
    }
    encoded = json.dumps(payload, ensure_ascii=False)
    assert "Yogurt V4 开发" in encoded
