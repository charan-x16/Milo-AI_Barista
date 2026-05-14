import pytest
from agentscope.message import Msg

from cafe.agents.memory import (
    CONVERSATION_MESSAGES_TABLE,
    DEFAULT_USER_ID,
    MEMORY_SUMMARIES_TABLE,
    load_memory,
)
from cafe.agents.memory.summaries.models import MemorySummaryInsert
from cafe.agents.memory.summaries.repositories import MemorySummaryRepository
from cafe.agents.specialist_tools import _build_specialist_context


@pytest.mark.asyncio
async def test_context_builder_includes_summary_preferences_and_recent_messages():
    session_id = "context-session"
    memory = load_memory(session_id, user_id=DEFAULT_USER_ID)
    await memory.clear()
    await memory.add(
        [
            Msg(
                "user",
                f"[session_id={session_id}] I prefer oat milk and no sugar",
                "user",
                metadata={"display_text": "I prefer oat milk and no sugar"},
            ),
            Msg("assistant", "Got it. I will remember that.", "assistant"),
        ]
    )

    repo = MemorySummaryRepository(
        memory.engine,
        summary_table=MEMORY_SUMMARIES_TABLE,
        message_table=CONVERSATION_MESSAGES_TABLE,
    )
    await repo.insert_summary(
        MemorySummaryInsert(
            id="context-summary-1",
            conversation_id=memory.conversation_id,
            user_id=DEFAULT_USER_ID,
            summary_version=1,
            checkpoint_message_count=2,
            source_message_start=1,
            source_message_end=2,
            previous_summary_id=None,
            summary_text="The user prefers oat milk and no sugar.",
            summary_json={
                "summary_text": "The user prefers oat milk and no sugar.",
                "preferences": ["prefers oat milk", "no sugar"],
                "important_facts": ["likes personalized drink suggestions"],
                "cart_order_context": ["no active order yet"],
                "unresolved_questions": [],
            },
            metadata={"kind": "checkpoint_summary"},
        )
    )

    enriched = await _build_specialist_context(
        session_id=session_id,
        user_id=DEFAULT_USER_ID,
        base_query="cold coffee options",
        memory_obj=memory,
    )

    assert "User request: cold coffee options" in enriched
    assert "User preferences:" in enriched
    assert "- prefers oat milk" in enriched
    assert "- no sugar" in enriched
    assert "Recent conversation:" in enriched
    assert "I prefer oat milk and no sugar" in enriched


@pytest.mark.asyncio
async def test_context_builder_handles_no_memory_gracefully():
    enriched = await _build_specialist_context(
        session_id="brand-new-context-session",
        user_id=DEFAULT_USER_ID,
        base_query="show menu",
    )

    assert "User request: show menu" in enriched
    assert "Specialist instruction:" in enriched
