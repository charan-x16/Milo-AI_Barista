"""Product Search specialist - handles menu RAG lookups."""

from pathlib import Path

from agentscope.agent import ReActAgent
from agentscope.memory import InMemoryMemory
from agentscope.tool import Toolkit, view_text_file

from cafe.agents.llm import make_chat_model
from cafe.agents.memory import make_multi_agent_formatter
from cafe.agents.prompts import PRODUCT_SEARCH_PROMPT
from cafe.config import get_settings
from cafe.tools.product_tools import (
    search_menu_attribute_knowledge,
    search_product_and_attribute_knowledge,
    search_product_knowledge,
)


_SKILL_DIR = Path(__file__).resolve().parents[2] / "skills" / "menu_navigation"


def _make_toolkit() -> Toolkit:
    tk = Toolkit()
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
