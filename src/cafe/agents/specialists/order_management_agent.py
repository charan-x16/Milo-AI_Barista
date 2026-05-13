"""Order Management specialist - handles order lifecycle operations."""

from functools import lru_cache
from pathlib import Path

from agentscope.agent import ReActAgent
from agentscope.memory import InMemoryMemory
from agentscope.tool import Toolkit, view_text_file

from cafe.agents.llm import make_chat_model
from cafe.agents.memory import make_multi_agent_formatter
from cafe.agents.prompts import ORDER_MANAGEMENT_PROMPT
from cafe.config import get_settings
from cafe.tools.order_tools import cancel_order, place_order, track_order

_SKILL_DIR = Path(__file__).resolve().parents[2] / "skills" / "order_lifecycle"


@lru_cache(maxsize=1)
def _make_toolkit() -> Toolkit:
    """Handle make toolkit.

    Returns:
        - return Toolkit - The return value.
    """
    tk = Toolkit()
    tk.register_tool_function(place_order)
    tk.register_tool_function(track_order)
    tk.register_tool_function(cancel_order)
    tk.register_tool_function(view_text_file)
    tk.register_agent_skill(str(_SKILL_DIR))
    return tk


def make_order_management_agent() -> ReActAgent:
    """Handle make order management agent.

    Returns:
        - return ReActAgent - The return value.
    """
    s = get_settings()
    return ReActAgent(
        name="OrderManagementAgent",
        sys_prompt=ORDER_MANAGEMENT_PROMPT,
        model=make_chat_model(s, agent_name="OrderManagementAgent"),
        formatter=make_multi_agent_formatter(s),
        toolkit=_make_toolkit(),
        memory=InMemoryMemory(),
        max_iters=s.specialist_max_iters,
    )
