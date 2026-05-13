"""Wraps each specialist ReActAgent as a callable tool function.

The Orchestrator's toolkit is built from these. Specialists are short-lived
because cart/order state lives in StateStore, not in specialist chat memory.
"""

import json
from contextvars import ContextVar
from inspect import isawaitable

from agentscope.message import Msg, TextBlock
from agentscope.tool import ToolResponse

from cafe.agents.memory import get_summary, load_memory
from cafe.agents.memory.summary_cache import get_cached_summary
from cafe.agents.specialists.cart_management_agent import make_cart_management_agent
from cafe.agents.specialists.customer_support_agent import make_customer_support_agent
from cafe.agents.specialists.order_management_agent import make_order_management_agent
from cafe.agents.specialists.product_search_agent import make_product_search_agent
from cafe.core.observability import observe_tool
from cafe.core.session_context import (
    build_session_context,
    extract_session_preferences,
    format_specialist_context,
    get_current_session_context,
)
from cafe.tools.product_tools import (
    reset_current_product_query,
    reset_current_product_session_id,
    set_current_product_query,
    set_current_product_session_id,
)

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
    """Set the current user request.

    Args:
        - query: str - The query value.

    Returns:
        - return Any - The return value.
    """
    return _CURRENT_USER_REQUEST.set(query)


def reset_current_user_request(token) -> None:
    """Reset the current user request.

    Args:
        - token: Any - The token value.

    Returns:
        - return None - The return value.
    """
    _CURRENT_USER_REQUEST.reset(token)


def set_current_session_id(session_id: str):
    """Set the current session id.

    Args:
        - session_id: str - The session id value.

    Returns:
        - return Any - The return value.
    """
    return _CURRENT_SESSION_ID.set(session_id)


def reset_current_session_id(token) -> None:
    """Reset the current session id.

    Args:
        - token: Any - The token value.

    Returns:
        - return None - The return value.
    """
    _CURRENT_SESSION_ID.reset(token)


def _is_short_confirmation(text: str) -> bool:
    """Return whether short confirmation.

    Args:
        - text: str - The text value.

    Returns:
        - return bool - The return value.
    """
    normalized = " ".join(text.casefold().strip().split())
    return normalized in {"yes", "yes please", "yeah", "yep", "sure", "ok", "okay"}


def _is_context_dependent_followup(text: str) -> bool:
    """Return whether context dependent followup.

    Args:
        - text: str - The text value.

    Returns:
        - return bool - The return value.
    """
    normalized = " ".join(text.casefold().strip().split())
    if not normalized:
        return False
    pronouns = {"all", "those", "these", "them", "that", "this", "it", "same"}
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

    Args:
        - query: str - The query value.

    Returns:
        - return str - The return value.
    """
    raw_user_request = _CURRENT_USER_REQUEST.get()
    if not raw_user_request:
        return query

    if _is_short_confirmation(raw_user_request) or _is_context_dependent_followup(
        raw_user_request
    ):
        return query

    context = _active_session_context()
    active_preferences = set(context.preferences if context else ())
    query_preferences = extract_session_preferences(query)
    raw_preferences = extract_session_preferences(raw_user_request)
    if active_preferences and query_preferences & active_preferences:
        return query
    if active_preferences and raw_preferences & active_preferences:
        return raw_user_request

    if raw_user_request:
        return raw_user_request
    return query


def _active_session_context():
    """Return the active session context for specialist calls.

    Returns:
        - return SessionContext | None - The active context.
    """
    current = get_current_session_context()
    if current is not None:
        return current

    session_id = _CURRENT_SESSION_ID.get()
    if not session_id:
        return None

    return build_session_context(session_id=session_id)


def _extract_text_blocks(content) -> str:
    """Handle extract text blocks.

    Args:
        - content: Any - The content value.

    Returns:
        - return str - The return value.
    """
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
    """Handle extract reply text.

    Args:
        - reply: Any - The reply value.

    Returns:
        - return str - The return value.
    """
    content = getattr(reply, "content", "") or ""

    if isinstance(content, str):
        return content
    return _extract_text_blocks(content)


async def _conversation_memory_summary(context) -> str:
    """Return the active cumulative summary for a specialist prompt.

    Args:
        - context: Any - The active session context.

    Returns:
        - return str - The summary text, if available.
    """
    if context is None:
        return ""

    cached = await get_cached_summary(context.user_id, context.session_id)
    if cached.found:
        return cached.summary.strip()

    try:
        memory = load_memory(context.session_id, user_id=context.user_id)
        summary_msg = await get_summary(memory)
    except Exception:
        return ""

    if summary_msg is None:
        return ""
    return _extract_text_blocks(getattr(summary_msg, "content", "")).strip()


def _extract_final_answer_data(text: str) -> str | None:
    """Handle extract final answer data.

    Args:
        - text: str - The text value.

    Returns:
        - return str | None - The return value.
    """
    marker = "FINAL_ANSWER_DATA:"
    if marker not in text:
        return None

    answer = text.split(marker, 1)[1].strip()
    if "\n\nUse the FINAL_ANSWER_DATA" in answer:
        answer = answer.split("\n\nUse the FINAL_ANSWER_DATA", 1)[0].strip()
    return answer or None


def _display_text_from_payload(text: str) -> str | None:
    """Handle display text from payload.

    Args:
        - text: str - The text value.

    Returns:
        - return str | None - The return value.
    """
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
    """Handle agent memory messages.

    Args:
        - agent: Any - The agent value.

    Returns:
        - return list - The return value.
    """
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
    """Prefer complete menu/tool display text over lossy agent summaries.

    Args:
        - agent: Any - The agent value.

    Returns:
        - return str | None - The return value.
    """
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


async def _ask(agent, query: str, *, include_context: bool = True) -> ToolResponse:
    """Send a query to a specialist and wrap the specialist's final reply.

    Args:
        - agent: Any - The agent value.
        - query: str - The query value.
        - include_context: bool - Whether to include session context.

    Returns:
        - return ToolResponse - The return value.
    """
    context = _active_session_context() if include_context else None
    memory_summary = await _conversation_memory_summary(context)
    message = format_specialist_context(
        context,
        query,
        memory_summary=memory_summary,
    )
    reply = await agent(Msg(name="orchestrator", content=message, role="user"))
    text = await _customer_ready_tool_text(agent) or _extract_reply_text(reply)

    if text.strip():
        return ToolResponse(content=[TextBlock(type="text", text=text)])

    return ToolResponse(content=[TextBlock(type="text", text=text or str(reply))])


@observe_tool("ask_product_agent")
async def ask_product_agent(query: str) -> ToolResponse:
    """Delegate a menu/product question to the Product Search specialist.

    Args:
        - query: str - The query value.

    Returns:
        - return ToolResponse - The return value.
    """
    effective_query = _current_product_tool_query(query)
    query_token = set_current_product_query(effective_query)
    session_token = set_current_product_session_id(_CURRENT_SESSION_ID.get())
    try:
        return await _ask(make_product_search_agent(), effective_query)
    finally:
        reset_current_product_query(query_token)
        reset_current_product_session_id(session_token)


@observe_tool("ask_cart_agent")
async def ask_cart_agent(query: str) -> ToolResponse:
    """Delegate a cart operation to the Cart Management specialist.

    Args:
        - query: str - The query value.

    Returns:
        - return ToolResponse - The return value.
    """
    return await _ask(make_cart_management_agent(), query)


@observe_tool("ask_order_agent")
async def ask_order_agent(query: str) -> ToolResponse:
    """Delegate an order operation to the Order Management specialist.

    Args:
        - query: str - The query value.

    Returns:
        - return ToolResponse - The return value.
    """
    return await _ask(make_order_management_agent(), query)


@observe_tool("ask_support_agent")
async def ask_support_agent(query: str) -> ToolResponse:
    """Delegate an FAQ to the Customer Support specialist.

    Args:
        - query: str - The query value.

    Returns:
        - return ToolResponse - The return value.
    """
    return await _ask(make_customer_support_agent(), query)


def reset_specialists() -> None:
    """For tests.

    Returns:
        - return None - The return value.
    """
    _AGENTS.clear()
