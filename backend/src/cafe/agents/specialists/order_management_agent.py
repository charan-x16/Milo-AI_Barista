"""Order Management specialist - handles order lifecycle operations."""

from pathlib import Path

from agentscope.agent import ReActAgent
from agentscope.formatter import OpenAIMultiAgentFormatter
from agentscope.memory import InMemoryMemory
from agentscope.model import OpenAIChatModel
from agentscope.tool import Toolkit, view_text_file

from cafe.agents.prompts import ORDER_MANAGEMENT_PROMPT
from cafe.config import get_settings
from cafe.tools.order_tools import cancel_order, place_order, track_order


_SKILL_DIR = Path(__file__).resolve().parents[2] / "skills" / "order_lifecycle"


def _make_toolkit() -> Toolkit:
    tk = Toolkit()
    tk.register_tool_function(place_order)
    tk.register_tool_function(track_order)
    tk.register_tool_function(cancel_order)
    tk.register_tool_function(view_text_file)
    tk.register_agent_skill(str(_SKILL_DIR))
    return tk


def make_order_management_agent() -> ReActAgent:
    s = get_settings()
    return ReActAgent(
        name="OrderManagementAgent",
        sys_prompt=ORDER_MANAGEMENT_PROMPT,
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
