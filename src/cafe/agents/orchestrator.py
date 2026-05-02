"""The supervisor agent. Tools are the four specialists."""

from agentscope.agent import ReActAgent
from agentscope.tool import Toolkit

from cafe.agents.llm import make_chat_model
from cafe.agents.memory import (
    DEFAULT_USER_ID,
    load_memory,
    make_chat_formatter,
    make_compression_config,
)
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


def make_orchestrator(
    session_id: str = "default_session",
    user_id: str = DEFAULT_USER_ID,
) -> ReActAgent:
    s = get_settings()
    return ReActAgent(
        name="Orchestrator",
        sys_prompt=ORCHESTRATOR_PROMPT,
        model=make_chat_model(s),
        formatter=make_chat_formatter(s),
        toolkit=_make_toolkit(),
        memory=load_memory(session_id=session_id, user_id=user_id, settings=s),
        max_iters=10,
        compression_config=make_compression_config(s),
    )
