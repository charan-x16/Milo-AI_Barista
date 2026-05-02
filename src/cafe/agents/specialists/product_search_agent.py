"""Product Search specialist - handles menu RAG lookups."""

import json
from pathlib import Path

from agentscope.agent import ReActAgent
from agentscope.message import TextBlock
from agentscope.memory import InMemoryMemory
from agentscope.tool import Toolkit, ToolResponse, view_text_file

from cafe.agents.llm import make_chat_model
from cafe.agents.memory import make_multi_agent_formatter
from cafe.agents.prompts import PRODUCT_SEARCH_PROMPT
from cafe.config import get_settings
from cafe.tools.product_tools import (
    browse_current_menu_request,
    find_current_menu_matches,
    filter_current_menu_by_price,
    list_current_menu_prices,
    recommend_current_menu_items,
    search_menu_attribute_knowledge,
    search_product_and_attribute_knowledge,
    search_product_knowledge,
)


_SKILL_DIR = Path(__file__).resolve().parents[2] / "skills" / "menu_navigation"


def _menu_answer_postprocess(_tool_call, tool_response: ToolResponse) -> ToolResponse | None:
    """Render menu answer tools as final-answer data for ProductSearchAgent."""
    text = "".join(
        block.get("text", "") if isinstance(block, dict) else getattr(block, "text", "")
        for block in tool_response.content
    )
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None

    if payload.get("success") is not True:
        return None

    data = payload.get("data") or {}
    display_text = data.get("display_text")
    if not display_text:
        return None
    response_kind = data.get("response_kind")

    if response_kind == "menu_sections":
        style_instruction = (
            "Use the FINAL_ANSWER_DATA exactly as the final customer response. "
            "Keep the heading, blank lines, top-level headings, and bullet "
            "lists. Do not convert the list into prose or inline text. Do not "
            "add a generic follow-up question."
        )
    else:
        style_instruction = (
            "Use the FINAL_ANSWER_DATA exactly as the final customer response. "
            "Keep the direct heading and list formatting. Do not start with "
            "'I found'. Do not add a generic follow-up question after a "
            "successful list."
        )

    rendered = (
        "FINAL_ANSWER_DATA:\n"
        f"{display_text}\n\n"
        f"{style_instruction}"
    )
    return ToolResponse(content=[TextBlock(type="text", text=rendered)])


def _make_toolkit() -> Toolkit:
    tk = Toolkit()
    tk.register_tool_function(
        browse_current_menu_request,
        postprocess_func=_menu_answer_postprocess,
    )
    tk.register_tool_function(
        find_current_menu_matches,
        postprocess_func=_menu_answer_postprocess,
    )
    tk.register_tool_function(
        filter_current_menu_by_price,
        postprocess_func=_menu_answer_postprocess,
    )
    tk.register_tool_function(
        list_current_menu_prices,
        postprocess_func=_menu_answer_postprocess,
    )
    tk.register_tool_function(
        recommend_current_menu_items,
        postprocess_func=_menu_answer_postprocess,
    )
    tk.register_tool_function(search_product_knowledge)
    tk.register_tool_function(search_menu_attribute_knowledge)
    tk.register_tool_function(search_product_and_attribute_knowledge)
    tk.register_tool_function(view_text_file)
    tk.register_agent_skill(str(_SKILL_DIR))
    return tk


def make_product_search_agent() -> ReActAgent:
    s = get_settings()
    return ReActAgent(
        name="ProductSearchAgent",
        sys_prompt=PRODUCT_SEARCH_PROMPT,
        model=make_chat_model(s),
        formatter=make_multi_agent_formatter(s),
        toolkit=_make_toolkit(),
        memory=InMemoryMemory(),
        max_iters=6,
    )
