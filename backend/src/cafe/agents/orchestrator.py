"""The supervisor agent. Tools are the four specialists."""

from agentscope.agent import ReActAgent
from agentscope.formatter import OpenAIChatFormatter
from agentscope.memory import InMemoryMemory
from agentscope.model import OpenAIChatModel
from agentscope.tool import Toolkit

from cafe.agents.prompts import ORCHESTRATOR_PROMPT
from cafe.agents.specialist_tools import (
    ask_cart_agent,
    ask_order_agent,
    ask_product_agent,
    ask_support_agent,
)
from cafe.config import get_settings


def _make_toolkit() -> Toolkit:
    tk = Toolkit()
    tk.register_tool_function(ask_product_agent)
    tk.register_tool_function(ask_cart_agent)
    tk.register_tool_function(ask_order_agent)
    tk.register_tool_function(ask_support_agent)
    return tk


def make_orchestrator() -> ReActAgent:
    s = get_settings()
    if not s.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY not set. Copy .env.example to .env.")
    return ReActAgent(
        name="Orchestrator",
        sys_prompt=ORCHESTRATOR_PROMPT,
        model=OpenAIChatModel(
            model_name=s.openai_model,
            api_key=s.openai_api_key,
            stream=False,
        ),
        formatter=OpenAIChatFormatter(),
        toolkit=_make_toolkit(),
        memory=InMemoryMemory(),
        max_iters=10,
    )
