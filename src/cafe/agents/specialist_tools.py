"""Wraps each cached specialist ReActAgent as a callable tool function.

The Orchestrator's toolkit is built from these. Specialist agents are reused
between requests, while their in-memory scratchpads are cleared for each lease.
"""

import asyncio
import json
import logging
import time
from contextvars import ContextVar
from inspect import isawaitable
from typing import Any, Literal

from agentscope.message import Msg, TextBlock
from agentscope.tool import ToolResponse

from cafe.agents.agent_cache import acquire_cached_agent, clear_agent_cache_memories
from cafe.agents.memory import COMPRESSED_MARK, DEFAULT_USER_ID, load_memory
from cafe.agents.memory.storage import (
    CONVERSATION_MESSAGES_TABLE,
    MEMORY_SUMMARIES_TABLE,
)
from cafe.agents.memory.summaries.repositories import MemorySummaryRepository
from cafe.tools.product_tools import reset_current_product_query, set_current_product_query
from cafe.tools.product_tools import reset_current_product_session_id, set_current_product_session_id


logger = logging.getLogger(__name__)
SpecialistType = Literal["product", "cart", "order", "support"]
_CURRENT_USER_REQUEST: ContextVar[str | None] = ContextVar(
    "current_user_request",
    default=None,
)
_CURRENT_SESSION_ID: ContextVar[str | None] = ContextVar(
    "current_session_id",
    default=None,
)
_CURRENT_USER_ID: ContextVar[str] = ContextVar(
    "current_user_id",
    default=DEFAULT_USER_ID,
)


def set_current_user_request(query: str):
    return _CURRENT_USER_REQUEST.set(query)


def reset_current_user_request(token) -> None:
    _CURRENT_USER_REQUEST.reset(token)


def set_current_session_id(session_id: str):
    return _CURRENT_SESSION_ID.set(session_id)


def reset_current_session_id(token) -> None:
    _CURRENT_SESSION_ID.reset(token)


def set_current_user_id(user_id: str):
    return _CURRENT_USER_ID.set(user_id or DEFAULT_USER_ID)


def reset_current_user_id(token) -> None:
    _CURRENT_USER_ID.reset(token)


def _list_items(value: Any, *, limit: int | None = None) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        items = [value]
    elif isinstance(value, list | tuple):
        items = [str(item) for item in value if str(item).strip()]
    else:
        items = [str(value)]
    if limit is not None:
        items = items[:limit]
    return [item.strip() for item in items if item.strip()]


def _message_text(msg: Msg) -> str:
    display_text = getattr(msg, "metadata", {}).get("display_text")
    if display_text:
        return str(display_text)

    try:
        text = msg.get_text_content()
    except Exception:
        text = _extract_text_blocks(getattr(msg, "content", ""))
    return str(text or "").strip()


def _append_bullets(parts: list[str], heading: str, values: list[str]) -> None:
    if values:
        parts.append(f"{heading}\n" + "\n".join(f"- {value}" for value in values))


async def _build_specialist_context(
    session_id: str,
    user_id: str,
    base_query: str,
    memory_obj=None,
) -> str:
    """Build a specialist query enriched with memory summary and recent chat."""
    context_parts = [f"User request: {base_query.strip()}"]

    try:
        memory = memory_obj or load_memory(session_id=session_id, user_id=user_id)
        await memory._create_table()
        summary_repo = MemorySummaryRepository(
            memory.engine,
            summary_table=MEMORY_SUMMARIES_TABLE,
            message_table=CONVERSATION_MESSAGES_TABLE,
        )

        summary_data = await summary_repo.latest_summary_data(memory.conversation_id)
        summary_row = await summary_repo.latest_summary(memory.conversation_id)

        if summary_data:
            summary_text = str(summary_data.get("summary_text") or "").strip()
            if not summary_text and summary_row:
                summary_text = str(summary_row.get("summary_text") or "").strip()
            if summary_text:
                context_parts.append(f"Memory summary:\n{summary_text[:800]}")

            _append_bullets(
                context_parts,
                "User preferences:",
                _list_items(summary_data.get("preferences"), limit=8),
            )
            _append_bullets(
                context_parts,
                "Recent context:",
                _list_items(summary_data.get("important_facts"), limit=5),
            )
            _append_bullets(
                context_parts,
                "Cart/order context:",
                _list_items(summary_data.get("cart_order_context"), limit=5),
            )
            _append_bullets(
                context_parts,
                "Unresolved questions:",
                _list_items(summary_data.get("unresolved_questions"), limit=3),
            )

        recent_messages = await memory.get_memory(
            exclude_mark=COMPRESSED_MARK,
            prepend_summary=False,
        )
        visible_recent = [
            msg for msg in recent_messages[-6:]
            if getattr(msg, "role", None) in {"user", "assistant"}
        ][-4:]
        if visible_recent:
            context_parts.append("Recent conversation:")
            for msg in visible_recent:
                role = "Customer" if msg.role == "user" else "Assistant"
                content = _message_text(msg)
                if len(content) > 200:
                    content = f"{content[:197]}..."
                context_parts.append(f"- {role}: {content}")

    except Exception as e:
        logger.warning(
            "[%s] Failed to build specialist context: %s",
            session_id,
            e,
            exc_info=True,
        )

    context_parts.append(
        "Specialist instruction: Use the context only when relevant. "
        "Personalize the answer and filter by stated preferences, budgets, "
        "dietary needs, and exclusions when applicable. Keep tool output and "
        "retrieved knowledge as the source of truth; do not invent menu, cart, "
        "order, or policy facts."
    )
    return "\n\n".join(part for part in context_parts if part.strip())


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


def _tool_response_text(response: ToolResponse) -> str:
    content = getattr(response, "content", None) or []
    if not content:
        return ""
    block = content[0]
    if isinstance(block, dict):
        return str(block.get("text", ""))
    return str(getattr(block, "text", "") or "")


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


async def _ask_cached(
    agent_type: str,
    label: str,
    query: str,
) -> ToolResponse:
    start = time.perf_counter()
    async with acquire_cached_agent(agent_type) as agent:
        agent_get_ms = (time.perf_counter() - start) * 1000
        session_id = _CURRENT_SESSION_ID.get() or "unknown"
        logger.info(
            "[%s] Got %s from cache in %.1fms",
            session_id,
            label,
            agent_get_ms,
        )
        return await _ask(agent, query)


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
    session_id = _CURRENT_SESSION_ID.get() or "unknown"
    user_id = _CURRENT_USER_ID.get()
    product_query = _current_product_tool_query(query)
    enriched_query = await _build_specialist_context(
        session_id=session_id,
        user_id=user_id,
        base_query=product_query,
    )
    logger.info(
        "[%s] Enriched ProductSearchAgent query:\n%s...",
        session_id,
        enriched_query[:300],
    )

    query_token = set_current_product_query(product_query)
    session_token = set_current_product_session_id(session_id)
    try:
        return await _ask_cached("product", "ProductSearchAgent", enriched_query)
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
    session_id = _CURRENT_SESSION_ID.get() or "unknown"
    enriched_query = await _build_specialist_context(
        session_id=session_id,
        user_id=_CURRENT_USER_ID.get(),
        base_query=query,
    )
    logger.info(
        "[%s] Enriched CartManagementAgent query:\n%s...",
        session_id,
        enriched_query[:300],
    )
    return await _ask_cached("cart", "CartManagementAgent", enriched_query)


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
    session_id = _CURRENT_SESSION_ID.get() or "unknown"
    enriched_query = await _build_specialist_context(
        session_id=session_id,
        user_id=_CURRENT_USER_ID.get(),
        base_query=query,
    )
    logger.info(
        "[%s] Enriched OrderManagementAgent query:\n%s...",
        session_id,
        enriched_query[:300],
    )
    return await _ask_cached("order", "OrderManagementAgent", enriched_query)


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
    session_id = _CURRENT_SESSION_ID.get() or "unknown"
    enriched_query = await _build_specialist_context(
        session_id=session_id,
        user_id=_CURRENT_USER_ID.get(),
        base_query=query,
    )
    logger.info(
        "[%s] Enriched CustomerSupportAgent query:\n%s...",
        session_id,
        enriched_query[:300],
    )
    return await _ask_cached("support", "CustomerSupportAgent", enriched_query)


async def ask_multiple_specialists(
    queries: list[dict[str, str]],
) -> ToolResponse:
    """Call multiple independent specialists in parallel.

    Args:
        queries: Items with `type` ("product", "cart", "order", "support")
            and `query` fields. Use only for independent work.

    Returns:
        Combined specialist responses with clear labels.
    """
    session_id = _CURRENT_SESSION_ID.get() or "unknown"
    if isinstance(queries, str):
        try:
            parsed = json.loads(queries)
        except json.JSONDecodeError:
            parsed = []
        queries = parsed if isinstance(parsed, list) else []

    specialist_map = {
        "product": ask_product_agent,
        "cart": ask_cart_agent,
        "order": ask_order_agent,
        "support": ask_support_agent,
    }

    scheduled: list[tuple[SpecialistType, str]] = []
    tasks = []
    for item in queries or []:
        if not isinstance(item, dict):
            continue
        spec_type = str(item.get("type", "")).strip().lower()
        query = str(item.get("query", "")).strip()
        if spec_type not in specialist_map:
            logger.warning("[%s] Unknown specialist type: %s", session_id, spec_type)
            continue
        if not query:
            logger.warning("[%s] Empty query for specialist type: %s", session_id, spec_type)
            continue

        scheduled.append((spec_type, query))  # type: ignore[arg-type]
        tasks.append(specialist_map[spec_type](query))

    if not tasks:
        return ToolResponse(
            content=[TextBlock(type="text", text="No valid specialist queries were provided.")]
        )

    logger.info(
        "[%s] Running %d specialists in parallel: %s",
        session_id,
        len(tasks),
        [spec_type for spec_type, _ in scheduled],
    )
    start = time.perf_counter()
    results = await asyncio.gather(*tasks, return_exceptions=True)
    elapsed_ms = (time.perf_counter() - start) * 1000
    logger.info(
        "[%s] Completed %d specialists in parallel in %.1fms",
        session_id,
        len(tasks),
        elapsed_ms,
    )

    combined_text = []
    for (spec_type, query), result in zip(scheduled, results, strict=False):
        label = spec_type.upper()
        if isinstance(result, Exception):
            logger.error(
                "[%s] %s specialist failed in parallel batch",
                session_id,
                label,
                exc_info=(type(result), result, result.__traceback__),
            )
            combined_text.append(f"[{label} ERROR]: {result}")
            continue

        text = _tool_response_text(result).strip()
        combined_text.append(
            f"[{label} RESPONSE]\nQuery: {query}\n{text or '(empty response)'}"
        )

    return ToolResponse(content=[TextBlock(type="text", text="\n\n".join(combined_text))])


def reset_specialists() -> None:
    """For tests."""
    clear_agent_cache_memories()
