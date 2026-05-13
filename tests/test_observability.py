"""Tests test observability module."""

from types import SimpleNamespace

import pytest

from cafe.core.observability import (
    ObservedChatModel,
    TurnObserver,
    observe_tool,
    reset_current_observer,
    set_current_observer,
)


@pytest.mark.asyncio
async def test_observed_chat_model_records_llm_tokens():
    """Verify observed chat model records llm tokens.

    Returns:
        - return Any - The return value.
    """

    class FakeUsage:
        input_tokens = 12
        output_tokens = 7
        time = 0.2

    class FakeModel:
        model_name = "fake-model"
        stream = False

        async def __call__(self, *args, **kwargs):
            """Verify call.

            Args:
                - args: Any - The args value.
                - kwargs: Any - The kwargs value.

            Returns:
                - return Any - The return value.
            """
            return SimpleNamespace(usage=FakeUsage())

    observer = TurnObserver(session_id="s1", user_id="u1", user_text="hello")
    token = set_current_observer(observer)
    try:
        response = await ObservedChatModel(
            FakeModel(),
            agent_name="Orchestrator",
        )([])
    finally:
        reset_current_observer(token)

    summary = observer.summary()
    assert response.usage.input_tokens == 12
    assert summary["llm_calls"] == 1
    assert summary["token_usage"]["input_tokens"] == 12
    assert summary["token_usage"]["output_tokens"] == 7
    assert "orchestrator_llm_1" in summary["latency_ms"]


@pytest.mark.asyncio
async def test_observe_tool_records_tool_latency():
    """Verify observe tool records tool latency.

    Returns:
        - return Any - The return value.
    """

    @observe_tool("demo_tool")
    async def demo_tool():
        """Verify demo tool.

        Returns:
            - return Any - The return value.
        """
        return "ok"

    observer = TurnObserver(session_id="s1", user_id="u1", user_text="hello")
    token = set_current_observer(observer)
    try:
        assert await demo_tool() == "ok"
    finally:
        reset_current_observer(token)

    summary = observer.summary()
    assert summary["tool_calls"] == 1
    assert "tool_demo_tool_1" in summary["latency_ms"]
