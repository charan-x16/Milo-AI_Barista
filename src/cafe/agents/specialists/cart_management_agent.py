"""Cart Management specialist - handles cart operations."""

from pathlib import Path

from agentscope.agent import ReActAgent
from agentscope.formatter import OpenAIMultiAgentFormatter
from agentscope.memory import InMemoryMemory
from agentscope.model import OpenAIChatModel
from agentscope.tool import Toolkit, view_text_file

from cafe.agents.prompts import CART_MANAGEMENT_PROMPT
from cafe.config import get_settings
from cafe.tools.cart_tools import add_to_cart, clear_cart, remove_from_cart, view_cart


_SKILL_DIR = Path(__file__).resolve().parents[2] / "skills" / "cart_etiquette"


def _make_toolkit() -> Toolkit:
    tk = Toolkit()
    tk.register_tool_function(add_to_cart)
    tk.register_tool_function(remove_from_cart)
    tk.register_tool_function(view_cart)
    tk.register_tool_function(clear_cart)
    tk.register_tool_function(view_text_file)
    tk.register_agent_skill(str(_SKILL_DIR))
    return tk


def make_cart_management_agent() -> ReActAgent:
    s = get_settings()
    return ReActAgent(
        name="CartManagementAgent",
        sys_prompt=CART_MANAGEMENT_PROMPT,
        model=OpenAIChatModel(
            model_name=s.openai_model,
            api_key=s.openai_api_key,
            stream=False,
        ),
        formatter=OpenAIMultiAgentFormatter(),
        toolkit=_make_toolkit(),
        memory=InMemoryMemory(),
        max_iters=6,
    )
