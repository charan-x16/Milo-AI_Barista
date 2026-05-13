from pathlib import Path

import pytest
from agentscope.message import Msg, ToolResultBlock

from cafe.agents.memory import storage as memory_storage
from cafe.agents.memory import (
    COMPRESSED_MARK,
    MEMORY_SUMMARIES_TABLE,
    MemorySummaryDraft,
    TOOL_RESULT_MARK,
    get_latest_memory_summary,
    get_recent_messages,
    get_summary,
    load_memory,
    maybe_generate_memory_summary,
)
from cafe.config import Settings


@pytest.fixture
def sqlite_memory_settings(tmp_path, monkeypatch):
    monkeypatch.setattr(memory_storage, "_ENGINE", None)
    db_path = tmp_path / "memory.sqlite3"
    return Settings(
        _env_file=None,
        memory_database_url=f"sqlite+aiosqlite:///{db_path.as_posix()}",
        memory_recent_messages=8,
        memory_summary_interval_messages=8,
    )


@pytest.mark.asyncio
async def test_app_sql_memory_stores_messages_and_marks(sqlite_memory_settings):
    memory = load_memory(
        "session-1",
        user_id="user-1",
        settings=sqlite_memory_settings,
    )
    await memory.clear()

    user_msg = Msg(
        "user",
        "[session_id=session-1] show menu",
        "user",
        metadata={"display_text": "show menu"},
    )
    assistant_msg = Msg("assistant", "Of course.", "assistant")
    tool_msg = Msg(
        "system",
        [
            ToolResultBlock(
                type="tool_result",
                id="tool-1",
                name="browse_menu",
                output="x" * 3000,
            )
        ],
        "system",
    )

    await memory.add([user_msg, assistant_msg, tool_msg])
    await memory.update_compressed_summary(
        "<conversation_summary>Key facts</conversation_summary>"
    )
    await memory.update_messages_mark("compressed", msg_ids=[user_msg.id])

    assert await memory.size() == 3
    assert [msg.id for msg in await memory.get_uncompressed_messages()] == [
        assistant_msg.id,
        tool_msg.id,
    ]
    tool_results = await memory.get_memory(
        mark=TOOL_RESULT_MARK,
        prepend_summary=False,
    )
    assert len(tool_results[0].content[0]["output"][0]["text"]) == 2000


def _visible_msg(index: int) -> Msg:
    role = "user" if index % 2 else "assistant"
    return Msg(role, f"{role} visible {index}", role)


async def _add_visible_messages(memory, start: int, end: int) -> None:
    await memory.add([_visible_msg(index) for index in range(start, end + 1)])


def test_memory_summaries_schema_is_registered():
    columns = set(MEMORY_SUMMARIES_TABLE.c.keys())

    assert columns == {
        "id",
        "conversation_id",
        "user_id",
        "summary_version",
        "checkpoint_message_count",
        "source_message_start",
        "source_message_end",
        "previous_summary_id",
        "summary_text",
        "summary_json",
        "metadata",
        "created_at",
        "updated_at",
    }
    assert MEMORY_SUMMARIES_TABLE.indexes == set()


def test_memory_summaries_migration_exists():
    create_migration = Path(
        "migrations/versions/20260513_0004_add_memory_summaries.py"
    ).read_text(encoding="utf-8")
    drop_migration = Path(
        "migrations/versions/20260513_0005_drop_memory_summary_unused_columns.py"
    ).read_text(encoding="utf-8")

    assert "create_table(" in create_migration
    assert '"memory_summaries"' in create_migration
    assert "uq_memory_summaries_conversation_checkpoint" in create_migration
    assert 'drop_column("session_id")' in drop_migration
    assert 'drop_column("is_active")' in drop_migration


@pytest.mark.asyncio
async def test_memory_summary_not_created_before_checkpoint(sqlite_memory_settings):
    memory = load_memory("summary-session", user_id="user-1", settings=sqlite_memory_settings)
    await memory.clear()
    await _add_visible_messages(memory, 1, 7)
    called = False

    async def summarizer(previous_summary, messages):
        nonlocal called
        called = True
        return MemorySummaryDraft("unused", {})

    summary = await maybe_generate_memory_summary(
        "summary-session",
        user_id="user-1",
        settings=sqlite_memory_settings,
        summarizer=summarizer,
    )

    assert summary is None
    assert called is False


@pytest.mark.asyncio
async def test_memory_summary_created_at_eight_visible_messages(sqlite_memory_settings):
    memory = load_memory("summary-session", user_id="user-1", settings=sqlite_memory_settings)
    await memory.clear()
    await _add_visible_messages(memory, 1, 8)
    captured = {}

    async def summarizer(previous_summary, messages):
        captured["previous"] = previous_summary
        captured["ordinals"] = [msg["ordinal"] for msg in messages]
        return MemorySummaryDraft(
            "summary v1",
            {"important_facts": ["first checkpoint"]},
        )

    summary = await maybe_generate_memory_summary(
        "summary-session",
        user_id="user-1",
        settings=sqlite_memory_settings,
        summarizer=summarizer,
    )

    assert summary["summary_version"] == 1
    assert summary["checkpoint_message_count"] == 8
    assert summary["source_message_start"] == 1
    assert summary["source_message_end"] == 8
    assert summary["summary_text"] == "summary v1"
    assert summary["summary_json"]["important_facts"] == ["first checkpoint"]
    assert captured == {"previous": None, "ordinals": list(range(1, 9))}


@pytest.mark.asyncio
async def test_memory_summary_at_sixteen_uses_previous_and_overlap(sqlite_memory_settings):
    memory = load_memory("summary-session", user_id="user-1", settings=sqlite_memory_settings)
    await memory.clear()
    await _add_visible_messages(memory, 1, 8)

    async def first_summarizer(previous_summary, messages):
        return MemorySummaryDraft("summary v1", {"version": 1})

    await maybe_generate_memory_summary(
        "summary-session",
        user_id="user-1",
        settings=sqlite_memory_settings,
        summarizer=first_summarizer,
    )
    await _add_visible_messages(memory, 9, 16)
    captured = {}

    async def second_summarizer(previous_summary, messages):
        captured["previous"] = previous_summary
        captured["ordinals"] = [msg["ordinal"] for msg in messages]
        return MemorySummaryDraft("summary v2", {"version": 2})

    summary = await maybe_generate_memory_summary(
        "summary-session",
        user_id="user-1",
        settings=sqlite_memory_settings,
        summarizer=second_summarizer,
    )

    assert summary["summary_version"] == 2
    assert summary["checkpoint_message_count"] == 16
    assert summary["source_message_start"] == 6
    assert summary["source_message_end"] == 16
    assert summary["previous_summary_id"]
    assert captured == {"previous": "summary v1", "ordinals": list(range(6, 17))}


@pytest.mark.asyncio
async def test_memory_summary_checkpoint_is_idempotent(sqlite_memory_settings):
    memory = load_memory("summary-session", user_id="user-1", settings=sqlite_memory_settings)
    await memory.clear()
    await _add_visible_messages(memory, 1, 8)
    calls = 0

    async def summarizer(previous_summary, messages):
        nonlocal calls
        calls += 1
        return MemorySummaryDraft(f"summary call {calls}", {"calls": calls})

    first = await maybe_generate_memory_summary(
        "summary-session",
        user_id="user-1",
        settings=sqlite_memory_settings,
        summarizer=summarizer,
    )
    second = await maybe_generate_memory_summary(
        "summary-session",
        user_id="user-1",
        settings=sqlite_memory_settings,
        summarizer=summarizer,
    )

    assert first["summary_text"] == "summary call 1"
    assert second is None
    assert calls == 1


@pytest.mark.asyncio
async def test_tool_messages_do_not_count_for_summary_checkpoint(sqlite_memory_settings):
    memory = load_memory("summary-session", user_id="user-1", settings=sqlite_memory_settings)
    await memory.clear()
    await _add_visible_messages(memory, 1, 7)
    await memory.add(
        Msg(
            "system",
            [
                ToolResultBlock(
                    type="tool_result",
                    id="tool-1",
                    name="browse_menu",
                    output="tool output",
                )
            ],
            "system",
        )
    )
    calls = 0

    async def summarizer(previous_summary, messages):
        nonlocal calls
        calls += 1
        return MemorySummaryDraft("summary", {})

    assert await maybe_generate_memory_summary(
        "summary-session",
        user_id="user-1",
        settings=sqlite_memory_settings,
        summarizer=summarizer,
    ) is None
    await memory.add(_visible_msg(8))
    summary = await maybe_generate_memory_summary(
        "summary-session",
        user_id="user-1",
        settings=sqlite_memory_settings,
        summarizer=summarizer,
    )

    assert summary["checkpoint_message_count"] == 8
    assert calls == 1


@pytest.mark.asyncio
async def test_latest_memory_summary_is_in_orchestrator_context(sqlite_memory_settings):
    memory = load_memory("summary-session", user_id="user-1", settings=sqlite_memory_settings)
    await memory.clear()
    await _add_visible_messages(memory, 1, 8)

    async def summarizer(previous_summary, messages):
        return MemorySummaryDraft("remember oat milk preference", {"preferences": ["oat milk"]})

    await maybe_generate_memory_summary(
        "summary-session",
        user_id="user-1",
        settings=sqlite_memory_settings,
        summarizer=summarizer,
    )

    assert (await get_latest_memory_summary(
        "summary-session",
        user_id="user-1",
        settings=sqlite_memory_settings,
    ))["summary_text"] == "remember oat milk preference"
    summary_msg = await get_summary(memory)
    recent = await get_recent_messages(memory)
    prompt_memory = await memory.get_memory(
        exclude_mark=COMPRESSED_MARK,
        prepend_summary=True,
    )

    assert summary_msg.get_text_content() == "remember oat milk preference"
    assert prompt_memory[0].get_text_content() == "remember oat milk preference"
    assert all(msg.role in {"user", "assistant"} for msg in recent)
