"""Tests test memory module."""

import pytest
from agentscope.message import Msg, ToolResultBlock

from cafe.agents.memory import (
    TOOL_RESULT_MARK,
    SummaryCheckpoint,
    compress_memory_after_turn,
    delete_session_data,
    get_summary,
    load_memory,
    should_compress_memory_after_turn,
)
from cafe.agents.memory import storage as memory_storage
from cafe.agents.memory.summary_cache import get_cached_summary
from cafe.config import Settings


@pytest.fixture
def sqlite_memory_settings(tmp_path, monkeypatch):
    """Verify sqlite memory settings.

    Args:
        - tmp_path: Any - The tmp path value.
        - monkeypatch: Any - The monkeypatch value.

    Returns:
        - return Any - The return value.
    """
    monkeypatch.setattr(memory_storage, "_ENGINE", None)
    db_path = tmp_path / "memory.sqlite3"
    return Settings(
        _env_file=None,
        memory_database_url=f"sqlite+aiosqlite:///{db_path.as_posix()}",
        memory_keep_recent_messages=8,
    )


@pytest.mark.asyncio
async def test_app_sql_memory_stores_messages_summary_and_marks(sqlite_memory_settings):
    """Verify app sql memory stores messages summary and marks.

    Args:
        - sqlite_memory_settings: Any - The sqlite memory settings value.

    Returns:
        - return None - The return value.
    """
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
    assert (await get_summary(memory)).get_text_content()
    assert [msg.id for msg in await memory.get_uncompressed_messages()] == [
        assistant_msg.id,
        tool_msg.id,
    ]
    tool_results = await memory.get_memory(
        mark=TOOL_RESULT_MARK,
        prepend_summary=False,
    )
    assert len(tool_results[0].content[0]["output"][0]["text"]) == 2000


@pytest.mark.asyncio
async def test_summary_cache_serves_active_session_without_sql(
    sqlite_memory_settings,
    monkeypatch,
):
    """Verify active summary cache avoids a repeated summary SQL read.

    Args:
        - sqlite_memory_settings: Any - The sqlite memory settings value.
        - monkeypatch: Any - The monkeypatch value.

    Returns:
        - return None - This test has no return value.
    """
    memory = load_memory(
        "summary-cache-session",
        user_id="user-1",
        settings=sqlite_memory_settings,
    )
    await memory.clear()
    await memory.update_compressed_summary("cached facts")

    fresh_memory = load_memory(
        "summary-cache-session",
        user_id="user-1",
        settings=sqlite_memory_settings,
    )

    async def fail_create_table():
        """Fail if cache hydration falls through to SQL setup.

        Returns:
            - return None - This helper has no return value.
        """
        raise AssertionError("summary cache miss caused SQL hydration")

    monkeypatch.setattr(fresh_memory, "_create_table", fail_create_table)

    summary = await get_summary(fresh_memory)

    assert summary.get_text_content() == "cached facts"


@pytest.mark.asyncio
async def test_delete_session_data_invalidates_summary_cache(sqlite_memory_settings):
    """Verify reset/delete removes the active summary cache entry.

    Args:
        - sqlite_memory_settings: Any - The sqlite memory settings value.

    Returns:
        - return None - This test has no return value.
    """
    memory = load_memory(
        "delete-cache-session",
        user_id="user-1",
        settings=sqlite_memory_settings,
    )
    await memory.clear()
    await memory.update_compressed_summary("stale facts")

    assert (await get_cached_summary("user-1", "delete-cache-session")).found is True

    await delete_session_data(
        "delete-cache-session",
        user_id="user-1",
        settings=sqlite_memory_settings,
    )

    assert (await get_cached_summary("user-1", "delete-cache-session")).found is False


@pytest.mark.asyncio
async def test_summary_checkpoint_waits_for_eight_messages(monkeypatch):
    """Verify summary checkpoint waits for the configured message count.

    Args:
        - monkeypatch: Any - The monkeypatch value.

    Returns:
        - return None - This test has no return value.
    """

    class Memory:
        async def next_summary_checkpoint(self, checkpoint_size):
            """Return no checkpoint yet.

            Returns:
                - return None - No checkpoint is ready.
            """
            assert checkpoint_size == 8
            return None

    class Agent:
        memory = Memory()

    should_compress = await should_compress_memory_after_turn(Agent())

    assert should_compress is False


@pytest.mark.asyncio
async def test_cumulative_summary_checkpoints_include_previous_summary(
    sqlite_memory_settings,
):
    """Verify every 8-message checkpoint builds on the previous summary.

    Args:
        - sqlite_memory_settings: Any - The sqlite memory settings value.

    Returns:
        - return None - This test has no return value.
    """
    memory = load_memory(
        "checkpoint-session",
        user_id="user-1",
        settings=sqlite_memory_settings,
    )
    await memory.clear()

    first_messages = [Msg("user", f"message {index}", "user") for index in range(8)]
    await memory.add(first_messages)

    class Agent:
        def __init__(self):
            """Initialize fake agent.

            Returns:
                - return None - This helper has no return value.
            """
            self.memory = memory
            self.calls: list[SummaryCheckpoint] = []

        async def summarize_memory_checkpoint(self, checkpoint):
            """Return deterministic cumulative summaries.

            Args:
                - checkpoint: SummaryCheckpoint - The checkpoint.

            Returns:
                - return str - The fake summary.
            """
            self.calls.append(checkpoint)
            if len(self.calls) == 1:
                assert checkpoint.previous_summary == ""
                assert [msg.get_text_content() for msg in checkpoint.messages] == [
                    f"message {index}" for index in range(8)
                ]
                return "summary through message 7"

            assert checkpoint.previous_summary == "summary through message 7"
            assert [msg.get_text_content() for msg in checkpoint.messages] == [
                f"message {index}" for index in range(8, 16)
            ]
            return "summary through message 15"

    agent = Agent()

    assert await compress_memory_after_turn(agent) is True
    assert (await get_summary(memory)).get_text_content() == "summary through message 7"
    assert await memory.get_uncompressed_messages() == []

    second_messages = [Msg("user", f"message {index}", "user") for index in range(8, 16)]
    await memory.add(second_messages)

    assert await compress_memory_after_turn(agent) is True
    assert (
        await get_summary(memory)
    ).get_text_content() == "summary through message 15"
    assert len(agent.calls) == 2


@pytest.mark.asyncio
async def test_app_sql_memory_add_batches_and_preserves_order(sqlite_memory_settings):
    """Verify batched memory add persists ordered messages.

    Args:
        - sqlite_memory_settings: Any - The sqlite memory settings value.

    Returns:
        - return None - This test has no return value.
    """
    memory = load_memory(
        "batch-session",
        user_id="user-1",
        settings=sqlite_memory_settings,
    )
    await memory.clear()
    messages = [
        Msg("user", "first", "user"),
        Msg("assistant", "second", "assistant"),
        Msg("user", "third", "user"),
    ]

    await memory.add(messages)

    stored = await memory.get_memory(prepend_summary=False)
    assert [msg.get_text_content() for msg in stored] == ["first", "second", "third"]


@pytest.mark.asyncio
async def test_current_turn_prompt_scope_keeps_orchestrator_memory_light(
    sqlite_memory_settings,
):
    """Verify routing memory exposes only current-turn messages to prompts.

    Args:
        - sqlite_memory_settings: Any - The sqlite memory settings value.

    Returns:
        - return None - This test has no return value.
    """
    memory = load_memory(
        "routing-session",
        user_id="user-1",
        settings=sqlite_memory_settings,
    )
    await memory.clear()
    old_msg = Msg("user", "old preference-heavy message", "user")
    await memory.add(old_msg)
    await memory.update_compressed_summary("old cumulative summary")

    routing_memory = load_memory(
        "routing-session",
        user_id="user-1",
        settings=sqlite_memory_settings,
        prompt_scope="current_turn",
    )
    current_msg = Msg("user", "current routed task", "user")
    routing_memory.begin_turn_capture()
    await routing_memory.add(current_msg)

    prompt_messages = await routing_memory.get_memory()

    assert [msg.get_text_content() for msg in prompt_messages] == [
        "current routed task"
    ]
    assert old_msg.id not in [msg.id for msg in prompt_messages]


@pytest.mark.asyncio
async def test_recent_window_preserves_tool_call_result_pair(sqlite_memory_settings):
    """Verify recent prompt windows do not orphan tool results.

    Args:
        - sqlite_memory_settings: Any - The sqlite memory settings value.

    Returns:
        - return None - This test has no return value.
    """
    memory = load_memory(
        "tool-pair-session",
        user_id="user-1",
        settings=sqlite_memory_settings,
    )
    await memory.clear()
    call = Msg(
        "assistant",
        [
            {
                "type": "tool_use",
                "id": "tool-1",
                "name": "lookup_menu",
                "input": {"query": "coffee"},
            }
        ],
        "assistant",
    )
    result = Msg(
        "system",
        [
            {
                "type": "tool_result",
                "id": "tool-1",
                "name": "lookup_menu",
                "output": [{"type": "text", "text": "Espresso"}],
            }
        ],
        "system",
    )

    fillers = [Msg("assistant", f"filler {index}", "assistant") for index in range(7)]
    await memory.add([Msg("user", "old", "user"), call, result, *fillers])

    recent = await memory.get_memory(
        exclude_mark=memory_storage.COMPRESSED_MARK,
        prepend_summary=False,
    )
    recent_ids = [msg.id for msg in recent]
    assert call.id in recent_ids
    assert result.id in recent_ids
    assert recent_ids.index(call.id) < recent_ids.index(result.id)
