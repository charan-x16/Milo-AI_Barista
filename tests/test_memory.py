import pytest
from agentscope.message import Msg, ToolResultBlock

from cafe.agents.memory import storage as memory_storage
from cafe.agents.memory import (
    TOOL_RESULT_MARK,
    get_summary,
    load_memory,
)
from cafe.config import Settings


@pytest.fixture
def sqlite_memory_settings(tmp_path, monkeypatch):
    monkeypatch.setattr(memory_storage, "_ENGINE", None)
    db_path = tmp_path / "memory.sqlite3"
    return Settings(
        _env_file=None,
        memory_database_url=f"sqlite+aiosqlite:///{db_path.as_posix()}",
        memory_keep_recent_messages=8,
    )


@pytest.mark.asyncio
async def test_app_sql_memory_stores_messages_summary_and_marks(sqlite_memory_settings):
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
