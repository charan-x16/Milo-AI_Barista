import json
from types import SimpleNamespace

import pytest

from cafe.agents import specialist_tools
from cafe.agents.memory import compress_memory_after_turn
from cafe.agents.specialist_tools import (
    _ask,
    _current_product_tool_query,
    _is_context_dependent_followup,
    reset_current_session_id,
    reset_current_user_request,
    set_current_user_request,
    set_current_session_id,
)
from cafe.agents.orchestrator import make_orchestrator
from cafe.agents.prompts import ORCHESTRATOR_PROMPT
from cafe.agents.session_manager import get_session_manager


def test_make_orchestrator_imports_without_api_key():
    assert callable(make_orchestrator)


def test_orchestrator_prompt_lists_specialist_tools():
    assert "ask_product_agent" in ORCHESTRATOR_PROMPT
    assert "ask_cart_agent" in ORCHESTRATOR_PROMPT
    assert "ask_order_agent" in ORCHESTRATOR_PROMPT
    assert "ask_support_agent" in ORCHESTRATOR_PROMPT


def test_specialist_cache_initially_empty():
    specialist_tools.reset_specialists()
    assert specialist_tools._AGENTS == {}


def test_get_session_manager_returns_singleton():
    assert get_session_manager() is get_session_manager()


def test_product_tool_query_prefers_raw_user_request():
    token = set_current_user_request("show me the coffee")
    try:
        query = _current_product_tool_query("Show coffee options")
    finally:
        reset_current_user_request(token)

    assert query == "show me the coffee"


def test_product_tool_query_uses_orchestrator_query_for_short_confirmation():
    token = set_current_user_request("yes please")
    try:
        query = _current_product_tool_query("Show vegan-friendly drink options")
    finally:
        reset_current_user_request(token)

    assert query == "Show vegan-friendly drink options"


def test_product_tool_query_uses_orchestrator_query_for_context_followup():
    token = set_current_user_request("show the prices for all")
    try:
        query = _current_product_tool_query("show prices for all Coffees")
    finally:
        reset_current_user_request(token)

    assert query == "show prices for all Coffees"


def test_current_session_context_round_trip():
    token = set_current_session_id("s123")
    try:
        from cafe.agents import specialist_tools

        assert specialist_tools._CURRENT_SESSION_ID.get() == "s123"
    finally:
        reset_current_session_id(token)


def test_detects_context_dependent_followup():
    assert _is_context_dependent_followup("show the prices for all") is True
    assert _is_context_dependent_followup("show me the coffees") is False


@pytest.mark.asyncio
async def test_post_turn_memory_compression_runs_when_recent_window_overflows():
    class CountedMemory:
        keep_recent = 8

        async def get_uncompressed_messages(self):
            return list(range(9))

    class Agent:
        def __init__(self):
            self.memory = CountedMemory()
            self.compression_config = SimpleNamespace(
                enable=True,
                trigger_threshold=60000,
                keep_recent=99,
            )
            self.compressed_with = None

        async def _compress_memory_if_needed(self):
            self.compressed_with = (
                self.compression_config.trigger_threshold,
                self.compression_config.keep_recent,
            )

    agent = Agent()

    assert await compress_memory_after_turn(agent) is True
    assert agent.compressed_with == (0, 8)
    assert agent.compression_config.trigger_threshold == 60000
    assert agent.compression_config.keep_recent == 99


@pytest.mark.asyncio
async def test_post_turn_memory_compression_skips_inside_recent_window():
    class CountedMemory:
        keep_recent = 8

        async def get_uncompressed_messages(self):
            return list(range(8))

    agent = SimpleNamespace(
        memory=CountedMemory(),
        compression_config=SimpleNamespace(
            enable=True,
            trigger_threshold=60000,
            keep_recent=8,
        ),
        _compress_memory_if_needed=lambda: None,
    )

    assert await compress_memory_after_turn(agent) is False


@pytest.mark.asyncio
async def test_specialist_returns_display_text_when_tool_result_is_customer_ready():
    display_text = "Here is the complete menu category list:\n\nBeverages:\n- Mocktails"
    agent_reply = "I found the menu sections. Which one would you like to explore?"
    tool_payload = json.dumps({"success": True, "data": {"display_text": display_text}, "error": None})

    class CategoryAgent:
        def __init__(self):
            self.memory = SimpleNamespace(get_memory=lambda: [
                SimpleNamespace(content=[
                    {
                        "type": "tool_result",
                        "name": "list_menu_categories",
                        "output": [{"type": "text", "text": tool_payload}],
                    }
                ])
            ])

        async def __call__(self, msg):
            return SimpleNamespace(content=agent_reply)

    response = await _ask(CategoryAgent(), "Show the menu")

    assert response.content[0]["text"] == display_text


@pytest.mark.asyncio
async def test_specialist_uses_agent_reply_without_display_text():
    agent_reply = "I found coffee sections."

    class SectionAgent:
        def __init__(self):
            self.memory = SimpleNamespace(get_memory=lambda: [])

        async def __call__(self, msg):
            return SimpleNamespace(content=agent_reply)

    response = await _ask(SectionAgent(), "Show coffees")

    assert response.content[0]["text"] == agent_reply


@pytest.mark.asyncio
async def test_specialist_section_item_uses_tool_display_text():
    display_text = "Here are the items under Coffees:\n- Espresso\n- Affogato"
    tool_payload = json.dumps({"success": True, "data": {"display_text": display_text}, "error": None})

    class SectionAgent:
        def __init__(self):
            self.memory = SimpleNamespace(get_memory=lambda: [
                SimpleNamespace(content=[
                    {
                        "type": "tool_result",
                        "name": "list_menu_section_items",
                        "output": [{"type": "text", "text": tool_payload}],
                    }
                ])
            ])

        async def __call__(self, msg):
            return SimpleNamespace(content="I found coffee sections.")

    response = await _ask(SectionAgent(), "Show coffees")

    assert response.content[0]["text"] == display_text


@pytest.mark.asyncio
async def test_specialist_uses_agent_reply_when_later_non_display_tool_runs():
    display_text = "Here is the complete menu, grouped by section:\n\nBeverages:\n- Coffees: Espresso"
    browse_payload = json.dumps({"success": True, "data": {"display_text": display_text}, "error": None})
    search_payload = json.dumps({"success": True, "data": {"results": [{"text": "Espresso | 99"}]}, "error": None})
    agent_reply = "Here are the items under ₹100:\n- Espresso - ₹99 [Coffee]"

    class MixedToolAgent:
        def __init__(self):
            self.memory = SimpleNamespace(get_memory=lambda: [
                SimpleNamespace(content=[
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
                ])
            ])

        async def __call__(self, msg):
            return SimpleNamespace(content=agent_reply)

    response = await _ask(MixedToolAgent(), "items under 100")

    assert response.content[0]["text"] == agent_reply


@pytest.mark.asyncio
async def test_specialist_keeps_display_text_when_later_helper_tool_runs():
    display_text = "Here is the complete menu, grouped by section:\n\nBeverages:\n- Coffees: Espresso"
    browse_payload = json.dumps({
        "success": True,
        "data": {
            "display_text": display_text,
            "response_kind": "menu_items",
            "passthrough": True,
        },
        "error": None,
    })
    helper_output = "The content of SKILL.md:\n```menu guidance```"

    class SkillHelperAgent:
        def __init__(self):
            self.memory = SimpleNamespace(get_memory=lambda: [
                SimpleNamespace(content=[
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
                ])
            ])

        async def __call__(self, msg):
            return SimpleNamespace(content="I found the menu. Want categories?")

    response = await _ask(SkillHelperAgent(), "show the menu please")

    assert response.content[0]["text"] == display_text


@pytest.mark.asyncio
async def test_specialist_uses_agent_reply_when_display_text_is_non_passthrough():
    display_text = "Here is the complete menu, grouped by section:\n\nBeverages:\n- Coffees: Espresso"
    tool_payload = json.dumps({
        "success": True,
        "data": {
            "display_text": display_text,
            "response_kind": "menu_items",
            "passthrough": False,
        },
        "error": None,
    })
    agent_reply = "I did not find a dedicated desserts section, but I can show sweet drinks."

    class FallbackBrowseAgent:
        def __init__(self):
            self.memory = SimpleNamespace(get_memory=lambda: [
                SimpleNamespace(content=[
                    {
                        "type": "tool_result",
                        "name": "browse_current_menu_request",
                        "output": [{"type": "text", "text": tool_payload}],
                    }
                ])
            ])

        async def __call__(self, msg):
            return SimpleNamespace(content=agent_reply)

    response = await _ask(FallbackBrowseAgent(), "any desserts?")

    assert response.content[0]["text"] == agent_reply


@pytest.mark.asyncio
async def test_specialist_returns_structured_match_display_text():
    browse_payload = json.dumps({
        "success": True,
        "data": {
            "display_text": "Here is the complete menu, grouped by section:\n\nBeverages:\n- Coffees",
            "response_kind": "menu_items",
            "passthrough": False,
        },
        "error": None,
    })
    match_text = (
        "I did not find a dedicated Desserts section, but I found these "
        "dessert-style menu items:\n- Affogato"
    )
    match_payload = json.dumps({
        "success": True,
        "data": {
            "display_text": match_text,
            "response_kind": "item_matches",
            "passthrough": True,
        },
        "error": None,
    })

    class ConceptMatchAgent:
        def __init__(self):
            self.memory = SimpleNamespace(get_memory=lambda: [
                SimpleNamespace(content=[
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
                ])
            ])

        async def __call__(self, msg):
            return SimpleNamespace(content="I found dessert options.")

    response = await _ask(ConceptMatchAgent(), "any desserts?")

    assert response.content[0]["text"] == match_text


@pytest.mark.asyncio
async def test_specialist_does_not_passthrough_empty_structured_match_result():
    match_payload = json.dumps({
        "success": True,
        "data": {
            "display_text": "I could not find menu items matching that request.",
            "items": [],
            "count": 0,
            "response_kind": "item_matches",
            "passthrough": False,
        },
        "error": None,
    })
    agent_reply = "I could not find unicorn snacks on the current menu."

    class EmptyMatchAgent:
        def __init__(self):
            self.memory = SimpleNamespace(get_memory=lambda: [
                SimpleNamespace(content=[
                    {
                        "type": "tool_result",
                        "name": "find_current_menu_matches",
                        "output": [{"type": "text", "text": match_payload}],
                    },
                ])
            ])

        async def __call__(self, msg):
            return SimpleNamespace(content=agent_reply)

    response = await _ask(EmptyMatchAgent(), "show unicorn snacks")

    assert response.content[0]["text"] == agent_reply


@pytest.mark.asyncio
async def test_specialist_returns_recommendation_display_text():
    display_text = "Representative picks from the current menu:\n- Espresso (INR 99; Coffees; Hot only)"
    tool_payload = json.dumps({
        "success": True,
        "data": {
            "display_text": display_text,
            "items": [{"name": "Espresso"}],
            "count": 1,
            "response_kind": "recommendations",
            "passthrough": True,
        },
        "error": None,
    })

    class RecommendationAgent:
        def __init__(self):
            self.memory = SimpleNamespace(get_memory=lambda: [
                SimpleNamespace(content=[
                    {
                        "type": "tool_result",
                        "name": "recommend_current_menu_items",
                        "output": [{"type": "text", "text": tool_payload}],
                    },
                ])
            ])

        async def __call__(self, msg):
            return SimpleNamespace(content="I recommend Espresso.")

    response = await _ask(RecommendationAgent(), "what do you recommend?")

    assert response.content[0]["text"] == display_text
