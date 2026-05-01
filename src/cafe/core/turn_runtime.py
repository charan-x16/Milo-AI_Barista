"""Agent turn runtime.

The runtime does not answer menu questions itself. It supplies session context,
lets the Orchestrator route to specialists, extracts the specialist result, and
returns that result when a single specialist owns the turn.
"""

from __future__ import annotations

import logging
from inspect import isawaitable
from typing import Any

from agentscope.message import Msg

from cafe.agents.session_manager import get_session_manager
from cafe.agents.specialist_tools import (
    reset_current_session_id,
    reset_current_user_request,
    set_current_session_id,
    set_current_user_request,
)
from cafe.core.debug_trace import get_debug_trace_store
from cafe.core.state import get_store


log = logging.getLogger(__name__)

MUTATING_TOOLS = {
    "ask_cart_agent",
    "ask_order_agent",
}

SPECIALIST_TOOLS = {
    "ask_product_agent",
    "ask_cart_agent",
    "ask_order_agent",
    "ask_support_agent",
}


def _build_context(session_id: str) -> str:
    """Compact snapshot the Orchestrator can use for routing."""
    store = get_store()
    cart = store.get_cart(session_id)
    recent_orders = [
        order for order in store.orders.values() if order.session_id == session_id
    ][-3:]
    parts = [f"[session_id={session_id}]"]

    if not cart.is_empty():
        parts.append(f"[cart: {len(cart.items)} item(s), INR {cart.total_inr}]")

    if recent_orders:
        ids = ", ".join(f"{order.order_id}({order.status})" for order in recent_orders)
        parts.append(f"[recent_orders: {ids}]")

    return " ".join(parts)


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
        msgs = agent.memory.get_memory()
        if isawaitable(msgs):
            msgs = await msgs
    except Exception:
        return []
    return list(msgs or [])


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
                calls.append({
                    "name": getattr(block, "name", None),
                    "input": getattr(block, "input", None),
                })
    return calls


def _extract_tool_results_from_messages(msgs: list) -> list[dict]:
    results: list[dict] = []
    for msg in msgs:
        content = getattr(msg, "content", []) or []
        if isinstance(content, str):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                results.append({
                    "name": block.get("name"),
                    "output": _extract_blocks_text(block.get("output")),
                })
            elif getattr(block, "type", None) == "tool_result":
                results.append({
                    "name": getattr(block, "name", None),
                    "output": _extract_blocks_text(getattr(block, "output", None)),
                })
    return results


def _single_specialist_result(
    tool_calls: list[dict],
    tool_results: list[dict],
) -> str | None:
    """Return the specialist answer for a single-specialist turn."""
    if len(tool_calls) != 1:
        return None

    tool_name = tool_calls[0].get("name")
    if tool_name not in SPECIALIST_TOOLS:
        return None

    for result in reversed(tool_results):
        if result.get("name") == tool_name:
            output = str(result.get("output", "")).strip()
            return output or None
    return None


def _assemble_reply(
    raw_orchestrator_reply: str,
    tool_calls: list[dict],
    tool_results: list[dict],
) -> tuple[str, str]:
    specialist_result = _single_specialist_result(tool_calls, tool_results)
    if specialist_result:
        return specialist_result, "specialist_result"
    return raw_orchestrator_reply, "orchestrator"


async def run_turn(
    session_id: str,
    user_text: str,
    enable_critic: bool = False,
) -> dict[str, Any]:
    """Route one user message and return the owning agent/tool answer."""
    trace = get_debug_trace_store()
    turn_id = trace.start_turn(session_id, user_text)
    trace.add_event(turn_id, "api", "running", "Chat request accepted")

    orchestrator = get_session_manager().get_or_create(session_id)
    trace.add_event(
        turn_id,
        "session_manager",
        "complete",
        "Loaded per-session Orchestrator",
        {"agent_name": getattr(orchestrator, "name", "Orchestrator")},
    )

    context = _build_context(session_id)
    trace.set_context(turn_id, context)
    trace.add_event(
        turn_id,
        "context",
        "complete",
        "Built session context snapshot",
        {"context": context},
    )

    msg = Msg(name="user", content=f"{context} {user_text}", role="user")
    memory_before = await _get_agent_memory(orchestrator)
    memory_before_count = len(memory_before)

    user_request_token = set_current_user_request(user_text)
    session_token = set_current_session_id(session_id)
    try:
        trace.add_event(turn_id, "orchestrator", "running", "Routing request")
        reply_msg = await orchestrator(msg)
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
        return {
            "reply": reply,
            "tool_calls": [],
            "critique": None,
        }
    finally:
        reset_current_user_request(user_request_token)
        reset_current_session_id(session_token)

    trace.add_event(turn_id, "orchestrator", "complete", "Routing complete")
    raw_orchestrator_reply = _extract_text(reply_msg)
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

    reply, source = _assemble_reply(raw_orchestrator_reply, tool_calls, tool_results)

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
    log.info("Final response source=%s preview=%r", source, reply[:160])
    trace.finish_turn(turn_id, "complete", reply, tool_calls, critique_payload)
    return {"reply": reply, "tool_calls": tool_calls, "critique": critique_payload}
