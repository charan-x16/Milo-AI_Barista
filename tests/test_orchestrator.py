"""Tests test orchestrator module."""

import json
from types import SimpleNamespace

import pytest
from agentscope.message import Msg

from cafe.agents import specialist_tools
from cafe.agents.memory import compress_memory_after_turn
from cafe.agents.orchestrator import make_orchestrator
from cafe.agents.prompts import ORCHESTRATOR_PROMPT
from cafe.agents.session_manager import get_session_manager
from cafe.agents.specialist_tools import (
    _ask,
    _current_product_tool_query,
    _is_context_dependent_followup,
    reset_current_session_id,
    reset_current_user_request,
    set_current_session_id,
    set_current_user_request,
)


def test_make_orchestrator_imports_without_api_key():
    """Verify make orchestrator imports without api key.

    Returns:
        - return None - The return value.
    """
    assert callable(make_orchestrator)


def test_make_orchestrator_uses_current_turn_memory(monkeypatch):
    """Verify Orchestrator prompt memory is scoped to the current turn.

    Args:
        - monkeypatch: Any - The monkeypatch value.

    Returns:
        - return None - This test has no return value.
    """
    from cafe.agents import orchestrator as orchestrator_module

    class FakeAgent:
        def __init__(self, **kwargs):
            """Capture ReActAgent construction kwargs.

            Args:
                - kwargs: Any - The kwargs value.

            Returns:
                - return None - This helper has no return value.
            """
            self.__dict__.update(kwargs)

    monkeypatch.setattr(orchestrator_module, "ReActAgent", FakeAgent)
    monkeypatch.setattr(orchestrator_module, "make_chat_model", lambda *_args, **_kwargs: object())

    agent = make_orchestrator("routing-memory-session", user_id="user-1")

    assert agent.memory.prompt_scope == "current_turn"


def test_orchestrator_prompt_lists_specialist_tools():
    """Verify orchestrator prompt lists specialist tools.

    Returns:
        - return None - The return value.
    """
    assert "ask_product_agent" in ORCHESTRATOR_PROMPT
    assert "ask_cart_agent" in ORCHESTRATOR_PROMPT
    assert "ask_order_agent" in ORCHESTRATOR_PROMPT
    assert "ask_support_agent" in ORCHESTRATOR_PROMPT


def test_specialist_cache_initially_empty():
    """Verify specialist cache initially empty.

    Returns:
        - return None - The return value.
    """
    specialist_tools.reset_specialists()
    assert specialist_tools._AGENTS == {}


def test_get_session_manager_returns_singleton():
    """Verify get session manager returns singleton.

    Returns:
        - return None - The return value.
    """
    assert get_session_manager() is get_session_manager()


def test_product_tool_query_prefers_raw_user_request():
    """Verify product tool query prefers raw user request.

    Returns:
        - return None - The return value.
    """
    token = set_current_user_request("show me the coffee")
    try:
        query = _current_product_tool_query("Show coffee options")
    finally:
        reset_current_user_request(token)

    assert query == "show me the coffee"


def test_product_tool_query_uses_orchestrator_query_for_short_confirmation():
    """Verify product tool query uses orchestrator query for short confirmation.

    Returns:
        - return None - The return value.
    """
    token = set_current_user_request("yes please")
    try:
        query = _current_product_tool_query("Show vegan-friendly drink options")
    finally:
        reset_current_user_request(token)

    assert query == "Show vegan-friendly drink options"


def test_product_tool_query_uses_orchestrator_query_for_context_followup():
    """Verify product tool query uses orchestrator query for context followup.

    Returns:
        - return None - The return value.
    """
    token = set_current_user_request("show the prices for all")
    try:
        query = _current_product_tool_query("show prices for all Coffees")
    finally:
        reset_current_user_request(token)

    assert query == "show prices for all Coffees"


def test_current_session_context_round_trip():
    """Verify current session context round trip.

    Returns:
        - return None - The return value.
    """
    token = set_current_session_id("s123")
    try:
        from cafe.agents import specialist_tools

        assert specialist_tools._CURRENT_SESSION_ID.get() == "s123"
    finally:
        reset_current_session_id(token)


def test_detects_context_dependent_followup():
    """Verify detects context dependent followup.

    Returns:
        - return None - The return value.
    """
    assert _is_context_dependent_followup("show the prices for all") is True
    assert _is_context_dependent_followup("show me the coffees") is False


@pytest.mark.asyncio
async def test_post_turn_memory_compression_runs_when_recent_window_overflows():
    """Verify post turn memory compression runs for summary checkpoints.

    Returns:
        - return Any - The return value.
    """

    class CountedMemory:
        def __init__(self):
            """Initialize the fake memory.

            Returns:
                - return None - This helper has no return value.
            """
            self.stored_summary = None

        async def next_summary_checkpoint(self, checkpoint_size):
            """Return a fake ready checkpoint.

            Args:
                - checkpoint_size: int - The checkpoint size.

            Returns:
                - return Any - The fake checkpoint.
            """
            assert checkpoint_size == 8
            from cafe.agents.memory import SummaryCheckpoint

            return SummaryCheckpoint(
                previous_summary="",
                messages=tuple(Msg("user", "x" * 20, "user") for _ in range(8)),
                message_ids=tuple(str(index) for index in range(8)),
                source_message_start=1,
                source_message_end=8,
            )

        async def store_summary_checkpoint(self, summary, checkpoint):
            """Store the generated checkpoint summary.

            Args:
                - summary: str - The summary.
                - checkpoint: Any - The checkpoint.

            Returns:
                - return None - This helper has no return value.
            """
            self.stored_summary = (summary, checkpoint.source_message_end)

    class Agent:
        def __init__(self):
            """Initialize the instance.

            Returns:
                - return None - The return value.
            """
            self.memory = CountedMemory()

        async def summarize_memory_checkpoint(self, checkpoint):
            """Return a deterministic fake LLM summary.

            Args:
                - checkpoint: Any - The checkpoint.

            Returns:
                - return str - The summary.
            """
            return f"summary through {checkpoint.source_message_end}"

    agent = Agent()

    assert await compress_memory_after_turn(agent) is True
    assert agent.memory.stored_summary == ("summary through 8", 8)


@pytest.mark.asyncio
async def test_post_turn_memory_compression_skips_inside_recent_window():
    """Verify post turn memory compression skips before checkpoint.

    Returns:
        - return Any - The return value.
    """

    class CountedMemory:
        async def next_summary_checkpoint(self, checkpoint_size):
            """Return no checkpoint.

            Args:
                - checkpoint_size: int - The checkpoint size.

            Returns:
                - return None - No checkpoint is ready.
            """
            assert checkpoint_size == 8
            return None

    agent = SimpleNamespace(
        memory=CountedMemory(),
    )

    assert await compress_memory_after_turn(agent) is False


@pytest.mark.asyncio
async def test_specialist_returns_display_text_when_tool_result_is_customer_ready():
    """Verify specialist returns display text when tool result is customer ready.

    Returns:
        - return Any - The return value.
    """
    display_text = "Here is the complete menu category list:\n\nBeverages:\n- Mocktails"
    agent_reply = "I found the menu sections. Which one would you like to explore?"
    tool_payload = json.dumps(
        {"success": True, "data": {"display_text": display_text}, "error": None}
    )

    class CategoryAgent:
        def __init__(self):
            """Initialize the instance.

            Returns:
                - return None - The return value.
            """
            self.memory = SimpleNamespace(
                get_memory=lambda: [
                    SimpleNamespace(
                        content=[
                            {
                                "type": "tool_result",
                                "name": "list_menu_categories",
                                "output": [{"type": "text", "text": tool_payload}],
                            }
                        ]
                    )
                ]
            )

        async def __call__(self, msg):
            """Verify call.

            Args:
                - msg: Any - The msg value.

            Returns:
                - return Any - The return value.
            """
            return SimpleNamespace(content=agent_reply)

    response = await _ask(CategoryAgent(), "Show the menu")

    assert response.content[0]["text"] == display_text


@pytest.mark.asyncio
async def test_specialist_uses_agent_reply_without_display_text():
    """Verify specialist uses agent reply without display text.

    Returns:
        - return Any - The return value.
    """
    agent_reply = "I found coffee sections."

    class SectionAgent:
        def __init__(self):
            """Initialize the instance.

            Returns:
                - return None - The return value.
            """
            self.memory = SimpleNamespace(get_memory=lambda: [])

        async def __call__(self, msg):
            """Verify call.

            Args:
                - msg: Any - The msg value.

            Returns:
                - return Any - The return value.
            """
            return SimpleNamespace(content=agent_reply)

    response = await _ask(SectionAgent(), "Show coffees")

    assert response.content[0]["text"] == agent_reply


@pytest.mark.asyncio
async def test_specialist_section_item_uses_tool_display_text():
    """Verify specialist section item uses tool display text.

    Returns:
        - return Any - The return value.
    """
    display_text = "Here are the items under Coffees:\n- Espresso\n- Affogato"
    tool_payload = json.dumps(
        {"success": True, "data": {"display_text": display_text}, "error": None}
    )

    class SectionAgent:
        def __init__(self):
            """Initialize the instance.

            Returns:
                - return None - The return value.
            """
            self.memory = SimpleNamespace(
                get_memory=lambda: [
                    SimpleNamespace(
                        content=[
                            {
                                "type": "tool_result",
                                "name": "list_menu_section_items",
                                "output": [{"type": "text", "text": tool_payload}],
                            }
                        ]
                    )
                ]
            )

        async def __call__(self, msg):
            """Verify call.

            Args:
                - msg: Any - The msg value.

            Returns:
                - return Any - The return value.
            """
            return SimpleNamespace(content="I found coffee sections.")

    response = await _ask(SectionAgent(), "Show coffees")

    assert response.content[0]["text"] == display_text


@pytest.mark.asyncio
async def test_specialist_uses_agent_reply_when_later_non_display_tool_runs():
    """Verify specialist uses agent reply when later non display tool runs.

    Returns:
        - return Any - The return value.
    """
    display_text = "Here is the complete menu, grouped by section:\n\nBeverages:\n- Coffees: Espresso"
    browse_payload = json.dumps(
        {"success": True, "data": {"display_text": display_text}, "error": None}
    )
    search_payload = json.dumps(
        {
            "success": True,
            "data": {"results": [{"text": "Espresso | 99"}]},
            "error": None,
        }
    )
    agent_reply = "Here are the items under ₹100:\n- Espresso - ₹99 [Coffee]"

    class MixedToolAgent:
        def __init__(self):
            """Initialize the instance.

            Returns:
                - return None - The return value.
            """
            self.memory = SimpleNamespace(
                get_memory=lambda: [
                    SimpleNamespace(
                        content=[
                            {
                                "type": "tool_result",
                                "name": "browse_current_menu_request",
                                "output": [{"type": "text", "text": browse_payload}],
                            },
                            {
                                "type": "tool_result",
                                "name": "search_product_knowledge",
                                "output": [{"type": "text", "text": search_payload}],
                            },
                        ]
                    )
                ]
            )

        async def __call__(self, msg):
            """Verify call.

            Args:
                - msg: Any - The msg value.

            Returns:
                - return Any - The return value.
            """
            return SimpleNamespace(content=agent_reply)

    response = await _ask(MixedToolAgent(), "items under 100")

    assert response.content[0]["text"] == agent_reply


@pytest.mark.asyncio
async def test_specialist_keeps_display_text_when_later_helper_tool_runs():
    """Verify specialist keeps display text when later helper tool runs.

    Returns:
        - return Any - The return value.
    """
    display_text = "Here is the complete menu, grouped by section:\n\nBeverages:\n- Coffees: Espresso"
    browse_payload = json.dumps(
        {
            "success": True,
            "data": {
                "display_text": display_text,
                "response_kind": "menu_items",
                "passthrough": True,
            },
            "error": None,
        }
    )
    helper_output = "The content of SKILL.md:\n```menu guidance```"

    class SkillHelperAgent:
        def __init__(self):
            """Initialize the instance.

            Returns:
                - return None - The return value.
            """
            self.memory = SimpleNamespace(
                get_memory=lambda: [
                    SimpleNamespace(
                        content=[
                            {
                                "type": "tool_result",
                                "name": "browse_current_menu_request",
                                "output": [{"type": "text", "text": browse_payload}],
                            },
                            {
                                "type": "tool_result",
                                "name": "view_text_file",
                                "output": [{"type": "text", "text": helper_output}],
                            },
                        ]
                    )
                ]
            )

        async def __call__(self, msg):
            """Verify call.

            Args:
                - msg: Any - The msg value.

            Returns:
                - return Any - The return value.
            """
            return SimpleNamespace(content="I found the menu. Want categories?")

    response = await _ask(SkillHelperAgent(), "show the menu please")

    assert response.content[0]["text"] == display_text


@pytest.mark.asyncio
async def test_specialist_uses_agent_reply_when_display_text_is_non_passthrough():
    """Verify specialist uses agent reply when display text is non passthrough.

    Returns:
        - return Any - The return value.
    """
    display_text = "Here is the complete menu, grouped by section:\n\nBeverages:\n- Coffees: Espresso"
    tool_payload = json.dumps(
        {
            "success": True,
            "data": {
                "display_text": display_text,
                "response_kind": "menu_items",
                "passthrough": False,
            },
            "error": None,
        }
    )
    agent_reply = (
        "I did not find a dedicated desserts section, but I can show sweet drinks."
    )

    class FallbackBrowseAgent:
        def __init__(self):
            """Initialize the instance.

            Returns:
                - return None - The return value.
            """
            self.memory = SimpleNamespace(
                get_memory=lambda: [
                    SimpleNamespace(
                        content=[
                            {
                                "type": "tool_result",
                                "name": "browse_current_menu_request",
                                "output": [{"type": "text", "text": tool_payload}],
                            }
                        ]
                    )
                ]
            )

        async def __call__(self, msg):
            """Verify call.

            Args:
                - msg: Any - The msg value.

            Returns:
                - return Any - The return value.
            """
            return SimpleNamespace(content=agent_reply)

    response = await _ask(FallbackBrowseAgent(), "any desserts?")

    assert response.content[0]["text"] == agent_reply


@pytest.mark.asyncio
async def test_specialist_returns_structured_match_display_text():
    """Verify specialist returns structured match display text.

    Returns:
        - return Any - The return value.
    """
    browse_payload = json.dumps(
        {
            "success": True,
            "data": {
                "display_text": "Here is the complete menu, grouped by section:\n\nBeverages:\n- Coffees",
                "response_kind": "menu_items",
                "passthrough": False,
            },
            "error": None,
        }
    )
    match_text = (
        "I did not find a dedicated Desserts section, but I found these "
        "dessert-style menu items:\n- Affogato"
    )
    match_payload = json.dumps(
        {
            "success": True,
            "data": {
                "display_text": match_text,
                "response_kind": "item_matches",
                "passthrough": True,
            },
            "error": None,
        }
    )

    class ConceptMatchAgent:
        def __init__(self):
            """Initialize the instance.

            Returns:
                - return None - The return value.
            """
            self.memory = SimpleNamespace(
                get_memory=lambda: [
                    SimpleNamespace(
                        content=[
                            {
                                "type": "tool_result",
                                "name": "browse_current_menu_request",
                                "output": [{"type": "text", "text": browse_payload}],
                            },
                            {
                                "type": "tool_result",
                                "name": "find_current_menu_matches",
                                "output": [{"type": "text", "text": match_payload}],
                            },
                        ]
                    )
                ]
            )

        async def __call__(self, msg):
            """Verify call.

            Args:
                - msg: Any - The msg value.

            Returns:
                - return Any - The return value.
            """
            return SimpleNamespace(content="I found dessert options.")

    response = await _ask(ConceptMatchAgent(), "any desserts?")

    assert response.content[0]["text"] == match_text


@pytest.mark.asyncio
async def test_specialist_does_not_passthrough_empty_structured_match_result():
    """Verify specialist does not passthrough empty structured match result.

    Returns:
        - return Any - The return value.
    """
    match_payload = json.dumps(
        {
            "success": True,
            "data": {
                "display_text": "I could not find menu items matching that request.",
                "items": [],
                "count": 0,
                "response_kind": "item_matches",
                "passthrough": False,
            },
            "error": None,
        }
    )
    agent_reply = "I could not find unicorn snacks on the current menu."

    class EmptyMatchAgent:
        def __init__(self):
            """Initialize the instance.

            Returns:
                - return None - The return value.
            """
            self.memory = SimpleNamespace(
                get_memory=lambda: [
                    SimpleNamespace(
                        content=[
                            {
                                "type": "tool_result",
                                "name": "find_current_menu_matches",
                                "output": [{"type": "text", "text": match_payload}],
                            },
                        ]
                    )
                ]
            )

        async def __call__(self, msg):
            """Verify call.

            Args:
                - msg: Any - The msg value.

            Returns:
                - return Any - The return value.
            """
            return SimpleNamespace(content=agent_reply)

    response = await _ask(EmptyMatchAgent(), "show unicorn snacks")

    assert response.content[0]["text"] == agent_reply


@pytest.mark.asyncio
async def test_specialist_returns_recommendation_display_text():
    """Verify specialist returns recommendation display text.

    Returns:
        - return Any - The return value.
    """
    display_text = "Representative picks from the current menu:\n- Espresso (INR 99; Coffees; Hot only)"
    tool_payload = json.dumps(
        {
            "success": True,
            "data": {
                "display_text": display_text,
                "items": [{"name": "Espresso"}],
                "count": 1,
                "response_kind": "recommendations",
                "passthrough": True,
            },
            "error": None,
        }
    )

    class RecommendationAgent:
        def __init__(self):
            """Initialize the instance.

            Returns:
                - return None - The return value.
            """
            self.memory = SimpleNamespace(
                get_memory=lambda: [
                    SimpleNamespace(
                        content=[
                            {
                                "type": "tool_result",
                                "name": "recommend_current_menu_items",
                                "output": [{"type": "text", "text": tool_payload}],
                            },
                        ]
                    )
                ]
            )

        async def __call__(self, msg):
            """Verify call.

            Args:
                - msg: Any - The msg value.

            Returns:
                - return Any - The return value.
            """
            return SimpleNamespace(content="I recommend Espresso.")

    response = await _ask(RecommendationAgent(), "what do you recommend?")

    assert response.content[0]["text"] == display_text
