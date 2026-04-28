"""Wraps each specialist ReActAgent as a callable tool function.

The Orchestrator's toolkit is built from these. Specialists are short-lived
because cart/order state lives in StateStore, not in specialist chat memory.
"""

from agentscope.message import Msg, TextBlock
from agentscope.tool import ToolResponse

from cafe.agents.specialists.cart_management_agent import make_cart_management_agent
from cafe.agents.specialists.customer_support_agent import make_customer_support_agent
from cafe.agents.specialists.order_management_agent import make_order_management_agent
from cafe.agents.specialists.product_search_agent import make_product_search_agent


# Kept for the reset helper/tests. Runtime specialist agents are short-lived.
_AGENTS: dict[str, object] = {}


async def _ask(agent, query: str) -> ToolResponse:
    """Send a query to a specialist and wrap its reply as a ToolResponse."""
    reply = await agent(Msg(name="orchestrator", content=query, role="user"))
    content = getattr(reply, "content", "") or ""

    if isinstance(content, str):
        text = content
    else:
        text = ""
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text += block.get("text", "")
            elif getattr(block, "type", None) == "text":
                text += getattr(block, "text", "")

    return ToolResponse(content=[TextBlock(type="text", text=text or str(reply))])


async def ask_product_agent(query: str) -> ToolResponse:
    """Delegate a menu/product question to the Product Search specialist.

    Args:
        query: A natural-language question about the menu. Include the
            session_id if relevant (e.g. "[session_id=s1] Find coffee under ₹150").

    Returns:
        The specialist's textual reply.

    Example:
        ask_product_agent(query="What hot drinks under ₹100 do you have?")
    """
    return await _ask(make_product_search_agent(), query)


async def ask_cart_agent(query: str) -> ToolResponse:
    """Delegate a cart operation to the Cart Management specialist.

    Args:
        query: Free-text cart instruction. MUST include the session_id like
            "[session_id=s1] Add 2 of m001".

    Returns:
        The specialist's textual reply.

    Example:
        ask_cart_agent(query="[session_id=s1] Show my cart")
    """
    return await _ask(make_cart_management_agent(), query)


async def ask_order_agent(query: str) -> ToolResponse:
    """Delegate an order operation to the Order Management specialist.

    Args:
        query: Instruction. Include session_id and any budget.

    Returns:
        The specialist's textual reply.

    Example:
        ask_order_agent(query="[session_id=s1] Place the order, budget ₹300")
    """
    return await _ask(make_order_management_agent(), query)


async def ask_support_agent(query: str) -> ToolResponse:
    """Delegate an FAQ to the Customer Support specialist.

    Args:
        query: User's question (hours, wifi, vegan, allergens, payment, etc.)

    Returns:
        The specialist's textual reply.

    Example:
        ask_support_agent(query="What are your hours?")
    """
    return await _ask(make_customer_support_agent(), query)


def reset_specialists() -> None:
    """For tests."""
    _AGENTS.clear()
