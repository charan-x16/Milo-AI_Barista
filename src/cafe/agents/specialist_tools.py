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
    """Prefer the raw user wording for canonical Product tools.

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


def _extract_reply_text(reply) -> str:
    content = getattr(reply, "content", "") or ""

    if isinstance(content, str):
        return content
    return _extract_text_blocks(content)


def _extract_final_answer_data(text: str) -> str | None:
    marker = "FINAL_ANSWER_DATA:"
    if marker not in text:
        return None

    answer = text.split(marker, 1)[1].strip()
    if "\n\nUse the FINAL_ANSWER_DATA" in answer:
        answer = answer.split("\n\nUse the FINAL_ANSWER_DATA", 1)[0].strip()
    return answer or None


def _display_text_from_payload(text: str) -> str | None:
    direct = _extract_final_answer_data(text)
    if direct:
        return direct

    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None

    data = payload.get("data") or {}
    display_text = data.get("display_text")
    if not display_text:
        return None

    if data.get("passthrough") is False or data.get("count") == 0:
        return None
    return str(display_text).strip() or None


async def _agent_memory_messages(agent) -> list:
    memory = getattr(agent, "memory", None)
    if memory is None:
        return []

    try:
        try:
            msgs = memory.get_memory(prepend_summary=False)
        except TypeError:
            msgs = memory.get_memory()
        if isawaitable(msgs):
            msgs = await msgs
    except Exception:
        return []
    return list(msgs or [])


async def _customer_ready_tool_text(agent) -> str | None:
    """Prefer complete menu/tool display text over lossy agent summaries."""
    candidate = None
    for msg in await _agent_memory_messages(agent):
        for block in getattr(msg, "content", []) or []:
            if not isinstance(block, dict) or block.get("type") != "tool_result":
                continue

            text = _extract_text_blocks(block.get("output"))
            if display_text := _display_text_from_payload(text):
                candidate = display_text
            elif block.get("name") != "view_text_file":
                candidate = None
    return candidate


async def _ask(agent, query: str) -> ToolResponse:
    """Send a query to a specialist and wrap the specialist's final reply."""
    reply = await agent(Msg(name="orchestrator", content=query, role="user"))
    text = await _customer_ready_tool_text(agent) or _extract_reply_text(reply)

    if text.strip():
        return ToolResponse(content=[TextBlock(type="text", text=text)])

    return ToolResponse(content=[TextBlock(type="text", text=text or str(reply))])


async def ask_product_agent(query: str) -> ToolResponse:
    """Delegate a menu/product question to the Product Search specialist.

    Args:
        query: A natural-language question about the menu. Include the
            session_id if relevant (e.g. "[session_id=s1] Find coffee under ₹150").

    Returns:
        A customer-ready menu/product answer. If it contains a list, category
        overview, price list, or item list, copy it exactly in the final
        customer response; do not rewrite it into prose or add a closing
        question.

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
        A customer-ready cart answer. Copy complete cart summaries exactly.

    Example:
        ask_cart_agent(query="[session_id=s1] Show my cart")
    """
    return await _ask(make_cart_management_agent(), query)


async def ask_order_agent(query: str) -> ToolResponse:
    """Delegate an order operation to the Order Management specialist.

    Args:
        query: Instruction. Include session_id and any budget.

    Returns:
        A customer-ready order answer. Copy complete order status or checkout
        summaries exactly.

    Example:
        ask_order_agent(query="[session_id=s1] Place the order, budget ₹300")
    """
    return await _ask(make_order_management_agent(), query)


async def ask_support_agent(query: str) -> ToolResponse:
    """Delegate an FAQ to the Customer Support specialist.

    Args:
        query: User's question (hours, wifi, vegan, allergens, payment, etc.)

    Returns:
        A customer-ready support answer. Copy exact policy answers without
        adding unsupported wording.

    Example:
        ask_support_agent(query="What are your hours?")
    """
    return await _ask(make_customer_support_agent(), query)


def reset_specialists() -> None:
    """For tests."""
    _AGENTS.clear()
