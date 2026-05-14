import asyncio
import time

import pytest
from agentscope.message import TextBlock
from agentscope.tool import ToolResponse

from cafe.agents import specialist_tools
from cafe.agents.specialist_tools import (
    ask_multiple_specialists,
    reset_current_session_id,
    set_current_session_id,
)


def _response_text(response: ToolResponse) -> str:
    block = response.content[0]
    if isinstance(block, dict):
        return block["text"]
    return block.text


@pytest.mark.asyncio
async def test_parallel_faster_than_sequential(monkeypatch):
    async def fake_support_agent(query: str) -> ToolResponse:
        await asyncio.sleep(0.05)
        return ToolResponse(
            content=[TextBlock(type="text", text=f"support answer: {query}")]
        )

    monkeypatch.setattr(specialist_tools, "ask_support_agent", fake_support_agent)
    token = set_current_session_id("test-parallel")
    try:
        start = time.perf_counter()
        await fake_support_agent("vegan options")
        await fake_support_agent("diabetic options")
        sequential_time = time.perf_counter() - start

        start = time.perf_counter()
        await ask_multiple_specialists(
            [
                {"type": "support", "query": "vegan options"},
                {"type": "support", "query": "diabetic options"},
            ]
        )
        parallel_time = time.perf_counter() - start

        assert parallel_time < sequential_time * 0.75
    finally:
        reset_current_session_id(token)


@pytest.mark.asyncio
async def test_parallel_combines_results(monkeypatch):
    async def fake_support_agent(query: str) -> ToolResponse:
        await asyncio.sleep(0)
        return ToolResponse(content=[TextBlock(type="text", text=f"policy: {query}")])

    monkeypatch.setattr(specialist_tools, "ask_support_agent", fake_support_agent)
    token = set_current_session_id("test-parallel-combine")
    try:
        result = await ask_multiple_specialists(
            [
                {"type": "support", "query": "vegan options"},
                {"type": "support", "query": "opening hours"},
            ]
        )
    finally:
        reset_current_session_id(token)

    text = _response_text(result)
    assert "[SUPPORT RESPONSE]" in text
    assert "vegan options" in text
    assert "opening hours" in text


@pytest.mark.asyncio
async def test_parallel_reports_invalid_queries():
    result = await ask_multiple_specialists(
        [{"type": "unknown", "query": "anything"}]
    )

    assert "No valid specialist queries" in _response_text(result)
