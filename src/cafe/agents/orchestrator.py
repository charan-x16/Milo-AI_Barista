"""The supervisor agent. Tools are the four specialists."""

from agentscope.agent import ReActAgent
from agentscope.tool import Toolkit

from cafe.agents.llm import make_chat_model
from cafe.agents.memory import (
    DEFAULT_USER_ID,
    load_memory,
    make_chat_formatter,
)
from cafe.agents.prompts import ORCHESTRATOR_PROMPT
from cafe.agents.specialist_tools import (
    ask_multiple_specialists,
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
    tk.register_tool_function(
        ask_multiple_specialists,
        func_name="ask_multiple_specialists",
        func_description=(
            "Call multiple independent specialists in parallel. Use this when "
            "a user request has separate product, cart, order, or support "
            "questions that do not depend on each other's results."
        ),
        json_schema={
            "type": "function",
            "function": {
                "name": "ask_multiple_specialists",
                "description": (
                    "Call multiple independent specialists in parallel. Each "
                    "query item must include type and query."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "queries": {
                            "type": "array",
                            "description": "Specialist queries to run in parallel.",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "type": {
                                        "type": "string",
                                        "enum": ["product", "cart", "order", "support"],
                                    },
                                    "query": {
                                        "type": "string",
                                        "description": "Query to send to the specialist.",
                                    },
                                },
                                "required": ["type", "query"],
                            },
                        }
                    },
                    "required": ["queries"],
                },
            },
        },
    )
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
    )
