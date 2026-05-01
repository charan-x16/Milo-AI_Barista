"""Wraps each specialist ReActAgent as a callable tool function.

The Orchestrator's toolkit is built from these. Specialists are short-lived
because cart/order state lives in StateStore, not in specialist chat memory.
"""

import json
from contextvars import ContextVar
from inspect import isawaitable

from agentscope.message import Msg, TextBlock
from agentscope.tool import ToolResponse

from cafe.agents.specialists.cart_management_agent import make_cart_management_agent
from cafe.agents.specialists.customer_support_agent import make_customer_support_agent
from cafe.agents.specialists.order_management_agent import make_order_management_agent
from cafe.agents.specialists.product_search_agent import make_product_search_agent
from cafe.tools.product_tools import reset_current_product_query, set_current_product_query
from cafe.tools.product_tools import reset_current_product_session_id, set_current_product_session_id


# Kept for the reset helper/tests. Runtime specialist agents are short-lived.
_AGENTS: dict[str, object] = {}
_CURRENT_USER_REQUEST: ContextVar[str | None] = ContextVar(
    "current_user_request",
    default=None,
)
_CURRENT_SESSION_ID: ContextVar[str | None] = ContextVar(
    "current_session_id",
    default=None,
)


def set_current_user_request(query: str):
    return _CURRENT_USER_REQUEST.set(query)


def reset_current_user_request(token) -> None:
    _CURRENT_USER_REQUEST.reset(token)


def set_current_session_id(session_id: str):
    return _CURRENT_SESSION_ID.set(session_id)


def reset_current_session_id(token) -> None:
    _CURRENT_SESSION_ID.reset(token)


def _is_short_confirmation(text: str) -> bool:
    normalized = " ".join(text.casefold().strip().split())
    return normalized in {"yes", "yes please", "yeah", "yep", "sure", "ok", "okay"}


def _is_context_dependent_followup(text: str) -> bool:
    normalized = " ".join(text.casefold().strip().split())
    if not normalized:
        return False
    pronouns = {"all", "those", "these", "them", "that", "it", "same"}
    words = set(normalized.split())
    if words & pronouns:
        return True
    return any(
        phrase in normalized
        for phrase in (
            "for all",
            "all of them",
            "their prices",
            "the prices",
            "with prices",
            "show prices",
            "show the prices",
        )
    )


def _current_product_tool_query(query: str) -> str:
    """Prefer the raw user wording for deterministic Product tools.

    The Orchestrator may broaden "show me the coffee" into "show coffee
    options". Direct menu browsing needs the user's exact words, while short
    confirmations and context-dependent follow-ups need the Orchestrator's
    expanded intent.
    """
    raw_user_request = _CURRENT_USER_REQUEST.get()
    if raw_user_request and not (
        _is_short_confirmation(raw_user_request)
        or _is_context_dependent_followup(raw_user_request)
    ):
        return raw_user_request
    return query


def _extract_text_blocks(content) -> str:
    if isinstance(content, str):
        return content

    text = ""
    for block in content or []:
        if isinstance(block, dict) and block.get("type") == "text":
            text += block.get("text", "")
        elif getattr(block, "type", None) == "text":
            text += getattr(block, "text", "")
    return text


async def _latest_display_text_from_tool(agent, tool_names: set[str]) -> str | None:
    try:
        msgs = agent.memory.get_memory()
        if isawaitable(msgs):
            msgs = await msgs
    except Exception:
        return None

    for msg in reversed(msgs[-12:]):
        content = getattr(msg, "content", []) or []
        if isinstance(content, str):
            continue

        for block in reversed(content):
            block_type = block.get("type") if isinstance(block, dict) else getattr(block, "type", None)
            block_name = block.get("name") if isinstance(block, dict) else getattr(block, "name", None)
            if block_type != "tool_result":
                continue

            output = block.get("output") if isinstance(block, dict) else getattr(block, "output", None)
            raw_text = _extract_text_blocks(output)
            try:
                payload = json.loads(raw_text)
            except json.JSONDecodeError:
                # Skill/helper tools can emit plain text after an answer tool.
                # They are not customer-facing answer evidence, so ignore them.
                continue

            if block_name not in tool_names:
                return None

            if payload.get("success") is True:
                data = payload.get("data") or {}
                if data.get("passthrough") is False:
                    return None
                display_text = data.get("display_text")
                if display_text:
                    return str(display_text)

    return None


async def _ask(agent, query: str) -> ToolResponse:
    """Send a query to a specialist and wrap its reply as a ToolResponse."""
    reply = await agent(Msg(name="orchestrator", content=query, role="user"))
    display_text = await _latest_display_text_from_tool(
        agent,
        {
            "browse_current_menu_request",
            "browse_menu",
            "filter_current_menu_by_price",
            "list_current_menu_prices",
            "list_menu_categories",
            "list_menu_section_items",
        },
    )
    if display_text:
        return ToolResponse(content=[TextBlock(type="text", text=display_text)])

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

    if text.strip():
        return ToolResponse(content=[TextBlock(type="text", text=text)])

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
    query_token = set_current_product_query(_current_product_tool_query(query))
    session_token = set_current_product_session_id(_CURRENT_SESSION_ID.get())
    try:
        return await _ask(make_product_search_agent(), query)
    finally:
        reset_current_product_query(query_token)
        reset_current_product_session_id(session_token)


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
