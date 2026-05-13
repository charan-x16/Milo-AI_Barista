"""Agent turn runtime.

The runtime does not answer menu questions itself. It supplies session context,
lets the Orchestrator route to specialists, extracts tool metadata for tracing,
and returns the Orchestrator's final answer.
"""

from __future__ import annotations

import asyncio
import logging
from inspect import isawaitable
from typing import Any

from agentscope.message import Msg

from cafe.agents.memory import DEFAULT_USER_ID
from cafe.agents.session_manager import get_session_manager
from cafe.agents.specialist_tools import (
    reset_current_session_id,
    reset_current_user_request,
    set_current_session_id,
    set_current_user_request,
)
from cafe.core.debug_trace import get_debug_trace_store
from cafe.core.intent_router import (
    Route,
    RouteResult,
    execute_route,
    route_message,
    schedule_fast_turn_persistence,
)
from cafe.core.observability import (
    TurnObserver,
    observed_span,
    reset_current_observer,
    set_current_observer,
)
from cafe.core.session_context import (
    build_session_context,
    format_orchestrator_context,
    record_session_preferences,
    reset_current_session_context,
    session_has_preferences,
    set_current_session_context,
)

log = logging.getLogger(__name__)

_SESSION_LOCKS: dict[tuple[str, str], asyncio.Lock] = {}
_SESSION_LOCKS_GUARD = asyncio.Lock()

MUTATING_TOOLS = {
    "ask_cart_agent",
    "ask_order_agent",
}

FORMAT_LOCKED_TOOLS = {
    "ask_product_agent",
}

DIRECT_RETURN_TOOLS = {
    "ask_product_agent",
    "ask_cart_agent",
    "ask_order_agent",
    "ask_support_agent",
}

NON_FINAL_SPECIALIST_PHRASES = (
    "would you like",
    "let me know",
    "how can i assist",
    "how can i help",
    "i found the menu",
    "we have many options",
    "i can help with",
)


def _route_needs_agentic_memory(route_name: str, session_id: str) -> bool:
    """Return whether a fast route should defer to memory-aware agents.

    Args:
        - route_name: str - The route name.
        - session_id: str - The active session id.

    Returns:
        - return bool - Whether to use the agentic path.
    """
    preference_sensitive = {
        "menu_browse",
        "categories",
        "beverages",
        "coffee",
        "cart_add",
        "order_place",
    }
    return route_name in preference_sensitive and session_has_preferences(session_id)


def _extract_text(msg) -> str:
    content = getattr(msg, "content", "") or ""
    if isinstance(content, str):
        return content

    parts = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text", ""))
        elif getattr(block, "type", None) == "text":
            parts.append(getattr(block, "text", ""))
    return "".join(parts) or str(msg)


def _extract_blocks_text(content) -> str:
    if isinstance(content, str):
        return content

    parts = []
    for block in content or []:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text", ""))
        elif getattr(block, "type", None) == "text":
            parts.append(getattr(block, "text", ""))
    return "".join(parts)


async def _get_agent_memory(agent) -> list:
    try:
        try:
            msgs = agent.memory.get_memory(prepend_summary=False)
        except TypeError:
            msgs = agent.memory.get_memory()
        if isawaitable(msgs):
            msgs = await msgs
    except Exception:
        return []
    return list(msgs or [])


async def _get_session_lock(user_id: str, session_id: str) -> asyncio.Lock:
    """One in-process request may run per user/session at a time."""
    key = (user_id, session_id)
    async with _SESSION_LOCKS_GUARD:
        if key not in _SESSION_LOCKS:
            _SESSION_LOCKS[key] = asyncio.Lock()
        return _SESSION_LOCKS[key]


def _extract_tool_calls_from_messages(msgs: list) -> list[dict]:
    calls: list[dict] = []
    for msg in msgs:
        content = getattr(msg, "content", []) or []
        if isinstance(content, str):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                calls.append({"name": block.get("name"), "input": block.get("input")})
            elif getattr(block, "type", None) == "tool_use":
                calls.append(
                    {
                        "name": getattr(block, "name", None),
                        "input": getattr(block, "input", None),
                    }
                )
    return calls


def _extract_tool_results_from_messages(msgs: list) -> list[dict]:
    results: list[dict] = []
    for msg in msgs:
        content = getattr(msg, "content", []) or []
        if isinstance(content, str):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                results.append(
                    {
                        "name": block.get("name"),
                        "output": _extract_blocks_text(block.get("output")),
                    }
                )
            elif getattr(block, "type", None) == "tool_result":
                results.append(
                    {
                        "name": getattr(block, "name", None),
                        "output": _extract_blocks_text(getattr(block, "output", None)),
                    }
                )
    return results


def _is_customer_ready_list(text: str) -> bool:
    """Detect complete specialist answers whose formatting should be preserved."""
    lines = [line.rstrip() for line in text.strip().splitlines()]
    bullet_count = sum(1 for line in lines if line.lstrip().startswith("- "))
    has_section_heading = any(
        line.endswith(":") and not line.lstrip().startswith("- ") for line in lines
    )
    return bullet_count >= 1 and has_section_heading


def _format_locked_reply(
    raw_reply: str,
    tool_calls: list[dict],
    tool_results: list[dict],
) -> tuple[str, str]:
    """Keep Product Search's complete list answers from being rewritten.

    This is not conversational bypass for arbitrary tool output. It is a final
    formatting guard for canonical customer-ready menu/list answers, where the
    specialist/tool owns the exact readable structure.
    """
    if len(tool_calls) != 1:
        return raw_reply, "orchestrator"

    tool_name = tool_calls[0].get("name")
    if tool_name not in FORMAT_LOCKED_TOOLS:
        return raw_reply, "orchestrator"

    for result in reversed(tool_results):
        if result.get("name") != tool_name:
            continue
        output = str(result.get("output", "")).strip()
        if output and _is_customer_ready_list(output):
            return output, "format_locked_tool_result"
        break

    return raw_reply, "orchestrator"


def _customer_ready_specialist_output(text: str, tool_name: str) -> bool:
    """Return whether a specialist result can be sent without rewriting.

    Args:
        - text: str - The specialist output text.
        - tool_name: str - The Orchestrator specialist tool name.

    Returns:
        - return bool - Whether the output is customer-ready.
    """
    stripped = text.strip()
    if not stripped:
        return False
    if stripped.startswith(("{", "[")):
        return False

    lower = stripped.casefold()
    if any(phrase in lower for phrase in NON_FINAL_SPECIALIST_PHRASES):
        return False

    if _is_customer_ready_list(stripped):
        return True

    if tool_name in {"ask_cart_agent", "ask_order_agent", "ask_support_agent"}:
        return True

    direct_starts = (
        "yes",
        "no",
        "not by default",
        "absolutely",
        "of course",
        "here",
        "sorry",
        "we ",
        "there ",
        "i did not",
        "no matching",
    )
    return lower.startswith(direct_starts) or "inr " in lower


def _direct_specialist_reply(
    tool_calls: list[dict],
    tool_results: list[dict],
) -> tuple[str, str] | None:
    """Return a direct specialist answer when the turn is single-specialist.

    Args:
        - tool_calls: list[dict] - Tool calls captured in the current turn.
        - tool_results: list[dict] - Tool results captured in the current turn.

    Returns:
        - return tuple[str, str] | None - Reply text and source, if direct.
    """
    if len(tool_calls) != 1:
        return None

    tool_name = str(tool_calls[0].get("name") or "")
    if tool_name not in DIRECT_RETURN_TOOLS:
        return None

    matching_results = [
        result for result in tool_results if result.get("name") == tool_name
    ]
    if len(matching_results) != 1:
        return None

    output = str(matching_results[0].get("output") or "").strip()
    if not _customer_ready_specialist_output(output, tool_name):
        return None

    return output, f"direct_specialist:{tool_name}"


def _captured_turn_messages(memory, fallback_messages: list | None = None) -> list:
    """Return currently captured turn messages without ending capture.

    Args:
        - memory: Any - The Orchestrator memory object.
        - fallback_messages: list | None - Fallback messages.

    Returns:
        - return list - Captured turn messages.
    """
    captured = getattr(memory, "_turn_messages", None)
    if captured is not None:
        return list(captured)
    return list(fallback_messages or [])


def _supports_manual_orchestrator_loop(orchestrator) -> bool:
    """Return whether an Orchestrator can be run one ReAct step at a time.

    Args:
        - orchestrator: Any - The Orchestrator object.

    Returns:
        - return bool - Whether manual loop methods are available.
    """
    return all(
        hasattr(orchestrator, attr) for attr in ("_reasoning", "_acting", "memory")
    )


def _initial_orchestrator_tool_choice(route_hint: RouteResult):
    """Return the Orchestrator's initial tool choice policy.

    Args:
        - route_hint: RouteResult - The router hint for this turn.

    Returns:
        - return str | None - AgentScope tool_choice value.
    """
    if route_hint.route is not Route.AGENT:
        return "required"

    specialist_reasons = (
        "menu",
        "category",
        "cart",
        "order",
        "faq",
        "support",
        "timing",
        "offer",
    )
    reason = route_hint.reason.casefold()
    if any(term in reason for term in specialist_reasons):
        return "required"
    return None


async def _maybe_run_retrieval_hooks(orchestrator, msg: Msg) -> None:
    """Run AgentScope retrieval hooks when they exist.

    Args:
        - orchestrator: Any - The Orchestrator object.
        - msg: Msg - The incoming user message.

    Returns:
        - return None - This function has no return value.
    """
    for hook_name in ("_retrieve_from_long_term_memory", "_retrieve_from_knowledge"):
        hook = getattr(orchestrator, hook_name, None)
        if hook is not None:
            await hook(msg)


async def _run_orchestrator_with_direct_specialist_return(
    orchestrator,
    msg: Msg,
    *,
    route_hint: RouteResult,
    initial_memory_count: int = 0,
) -> Msg:
    """Run Orchestrator and skip final rewrite for customer-ready results.

    Args:
        - orchestrator: Any - The Orchestrator agent.
        - msg: Msg - The customer message.
        - route_hint: RouteResult - The initial deterministic route hint.
        - initial_memory_count: int - Memory size before the turn.

    Returns:
        - return Msg - The final message for this turn.
    """
    if not _supports_manual_orchestrator_loop(orchestrator):
        return await orchestrator(msg)

    memory = orchestrator.memory
    await memory.add(msg)
    await _maybe_run_retrieval_hooks(orchestrator, msg)
    tool_choice = _initial_orchestrator_tool_choice(route_hint)

    for _ in range(getattr(orchestrator, "max_iters", 1)):
        msg_reasoning = await orchestrator._reasoning(tool_choice)
        tool_choice = None
        tool_uses = msg_reasoning.get_content_blocks("tool_use")

        if not tool_uses:
            return msg_reasoning

        for tool_call in tool_uses:
            await orchestrator._acting(tool_call)

        fallback_messages = []
        if not hasattr(memory, "_turn_messages"):
            memory_after = await _get_agent_memory(orchestrator)
            fallback_messages = memory_after[initial_memory_count:]

        current_messages = _captured_turn_messages(memory, fallback_messages)
        tool_calls = _extract_tool_calls_from_messages(current_messages)
        tool_results = _extract_tool_results_from_messages(current_messages)
        direct_reply = _direct_specialist_reply(tool_calls, tool_results)
        if direct_reply:
            reply, source = direct_reply
            reply_msg = Msg(
                name=getattr(orchestrator, "name", "Orchestrator"),
                content=reply,
                role="assistant",
                metadata={"reply_source": source},
            )
            await memory.add(reply_msg)
            return reply_msg

    summarizer = getattr(orchestrator, "_summarizing", None)
    if summarizer is not None:
        reply_msg = await summarizer()
        await memory.add(reply_msg)
        return reply_msg

    return Msg(
        name=getattr(orchestrator, "name", "Orchestrator"),
        content="Sorry, I could not complete that request.",
        role="assistant",
    )


async def run_turn(
    session_id: str,
    user_text: str,
    enable_critic: bool = False,
    user_id: str = DEFAULT_USER_ID,
) -> dict[str, Any]:
    """Route one user message and return the owning agent/tool answer."""
    observer = TurnObserver(
        session_id=session_id,
        user_id=user_id,
        user_text=user_text,
    )
    observer_token = set_current_observer(observer)
    try:
        lock = await _get_session_lock(user_id, session_id)
        async with lock:
            return await _run_turn_locked(
                session_id,
                user_text,
                enable_critic,
                user_id,
                observer,
            )
    except Exception as e:
        observer.set_result(
            status="error",
            reply_source=None,
            tool_calls=[],
            error=str(e),
        )
        raise
    finally:
        observer.log_summary()
        reset_current_observer(observer_token)


async def _run_turn_locked(
    session_id: str,
    user_text: str,
    enable_critic: bool,
    user_id: str,
    observer: TurnObserver,
) -> dict[str, Any]:
    """Execute one serialized turn for a session."""
    trace = get_debug_trace_store()
    turn_id = trace.start_turn(session_id, user_text)
    trace.add_event(turn_id, "api", "running", "Chat request accepted")

    record_session_preferences(session_id, user_text)
    route = route_message(user_text)
    route_hint = route
    if route.is_fast_path and _route_needs_agentic_memory(
        route.route.value, session_id
    ):
        route = RouteResult(
            route=Route.AGENT,
            reason="session preferences require memory-aware routing",
        )
    trace.add_event(
        turn_id,
        "router",
        "complete",
        "Checked deterministic intent router",
        {"route": route.route.value, "reason": route.reason},
    )
    if route.is_fast_path:
        with observed_span("tool", f"fast.{route.route.value}"):
            reply = await execute_route(route, session_id, user_text, user_id)
        schedule_fast_turn_persistence(
            session_id=session_id,
            user_id=user_id,
            user_text=user_text,
            reply=reply,
            route=route.route,
        )
        trace.add_event(
            turn_id,
            "response",
            "complete",
            "Deterministic fast path returned response",
            {"source": f"fast_path:{route.route.value}"},
        )
        trace.finish_turn(turn_id, "complete", reply, [], None)
        observer.set_result(
            status="complete",
            reply_source=f"fast_path:{route.route.value}",
            tool_calls=[],
            intent=route.route.value,
            fallback_path=f"intent_router->{route.route.value}",
        )
        return {
            "request_id": observer.request_id,
            "reply": reply,
            "tool_calls": [],
            "critique": None,
            "needs_compression": False,
        }

    if user_id == DEFAULT_USER_ID:
        orchestrator = get_session_manager().get_or_create(session_id)
    else:
        orchestrator = get_session_manager().get_or_create(
            session_id,
            user_id=user_id,
        )
    trace.add_event(
        turn_id,
        "session_manager",
        "complete",
        "Loaded SQL-backed per-session Orchestrator",
        {
            "agent_name": getattr(orchestrator, "name", "Orchestrator"),
            "user_id": user_id,
        },
    )

    session_context = build_session_context(user_id=user_id, session_id=session_id)
    context_text = format_orchestrator_context(session_context)
    trace.set_context(turn_id, context_text)
    trace.add_event(
        turn_id,
        "context",
        "complete",
        "Built session context snapshot",
        {
            "context": context_text,
            "preferences": list(session_context.preferences),
            "last_menu_scope": session_context.last_menu_scope,
        },
    )

    msg = Msg(
        name="user",
        content=f"{context_text} {user_text}",
        role="user",
        metadata={"display_text": user_text},
    )
    memory = getattr(orchestrator, "memory", None)
    capture_supported = hasattr(memory, "begin_turn_capture") and hasattr(
        memory,
        "consume_turn_capture",
    )
    memory_before_count = 0
    if capture_supported:
        memory.begin_turn_capture()
    else:
        memory_before = await _get_agent_memory(orchestrator)
        memory_before_count = len(memory_before)

    user_request_token = set_current_user_request(user_text)
    session_token = set_current_session_id(session_id)
    session_context_token = set_current_session_context(session_context)
    try:
        trace.add_event(turn_id, "orchestrator", "running", "Routing request")
        reply_msg = await _run_orchestrator_with_direct_specialist_return(
            orchestrator,
            msg,
            route_hint=route_hint,
            initial_memory_count=memory_before_count,
        )
    except Exception as e:
        log.exception("orchestrator failed")
        reply = f"Sorry, something went wrong: {e}"
        trace.add_event(
            turn_id,
            "orchestrator",
            "error",
            "Orchestrator raised an exception",
            {"error": str(e)},
        )
        trace.finish_turn(turn_id, "error", reply, [], None)
        observer.set_result(
            status="error",
            reply_source="orchestrator",
            tool_calls=[],
            intent="orchestrator_error",
            fallback_path="orchestrator",
            error=str(e),
        )
        return {
            "request_id": observer.request_id,
            "reply": reply,
            "tool_calls": [],
            "critique": None,
            "needs_compression": False,
        }
    finally:
        reset_current_user_request(user_request_token)
        reset_current_session_id(session_token)
        reset_current_session_context(session_context_token)

    trace.add_event(turn_id, "orchestrator", "complete", "Routing complete")
    raw_orchestrator_reply = _extract_text(reply_msg)
    direct_reply_source = getattr(reply_msg, "metadata", {}).get("reply_source")
    if capture_supported:
        current_turn_messages = memory.consume_turn_capture()
    else:
        memory_after = await _get_agent_memory(orchestrator)
        current_turn_messages = memory_after[memory_before_count:] or memory_after[-12:]
    tool_calls = _extract_tool_calls_from_messages(current_turn_messages)
    tool_results = _extract_tool_results_from_messages(current_turn_messages)
    trace.add_event(
        turn_id,
        "tools",
        "complete",
        f"Extracted {len(tool_calls)} tool call(s)",
        {"tool_calls": tool_calls},
    )

    reply, source = _format_locked_reply(
        raw_orchestrator_reply,
        tool_calls,
        tool_results,
    )
    if direct_reply_source and reply == raw_orchestrator_reply:
        source = direct_reply_source

    mutated = any(call["name"] in MUTATING_TOOLS for call in tool_calls)
    critique_payload = None
    if enable_critic and mutated:
        critique_payload = {"verdict": "PASS", "reason": "critic not yet enabled"}
        trace.add_event(
            turn_id,
            "critic",
            "complete",
            "Critic placeholder returned PASS",
            {"critique": critique_payload},
        )
    else:
        trace.add_event(
            turn_id,
            "critic",
            "skipped",
            "Critic skipped",
            {"enable_critic": enable_critic, "mutated": mutated},
        )

    trace.add_event(
        turn_id,
        "response",
        "complete",
        "Final response assembled",
        {"source": source},
    )

    needs_compression = True
    trace.add_event(
        turn_id,
        "memory",
        "scheduled",
        "Memory summary checkpoint deferred to background",
    )

    log.info("Final response source=%s preview=%r", source, reply[:160])
    trace.finish_turn(turn_id, "complete", reply, tool_calls, critique_payload)
    observer.set_result(
        status="complete",
        reply_source=source,
        tool_calls=tool_calls,
        fallback_path="orchestrator",
    )
    return {
        "request_id": observer.request_id,
        "reply": reply,
        "tool_calls": tool_calls,
        "critique": critique_payload,
        "needs_compression": needs_compression,
    }
