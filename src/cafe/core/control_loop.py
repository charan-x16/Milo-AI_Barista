"""One user message -> one assistant reply.

7-step flow:
  1. task_classification  - Orchestrator's first ReAct thought
  2. context_retrieval    - _build_context (cart snapshot, recent orders)
  3. planning             - built into ReAct loop
  4. execution loop       - await orchestrator(msg)
  5. tool_calls           - specialists call domain tools
  6. validation           - services raise; tools wrap as fail; (TODO: critic)
  7. state_update + output - assemble reply
"""

import logging
from inspect import isawaitable
from typing import Any

from agentscope.message import Msg

from cafe.agents.specialist_tools import (
    reset_current_session_id,
    reset_current_user_request,
    set_current_session_id,
    set_current_user_request,
)
from cafe.agents.session_manager import get_session_manager
from cafe.core.debug_trace import get_debug_trace_store
from cafe.core.deterministic_menu import deterministic_menu_reply
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


# Step 2: context retrieval
def _build_context(session_id: str) -> str:
    """Compact snapshot the Orchestrator can read at the top of the turn."""
    store = get_store()
    cart = store.get_cart(session_id)
    recent_orders = [
        order for order in store.orders.values() if order.session_id == session_id
    ][-3:]
    parts = [f"[session_id={session_id}]"]

    if not cart.is_empty():
        parts.append(f"[cart: {len(cart.items)} item(s), ₹{cart.total_inr}]")

    if recent_orders:
        ids = ", ".join(f"{order.order_id}({order.status})" for order in recent_orders)
        parts.append(f"[recent_orders: {ids}]")

    return " ".join(parts)


# Helpers
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


async def _extract_tool_calls(agent) -> list[dict]:
    return _extract_tool_calls_from_messages((await _get_agent_memory(agent))[-12:])


async def _extract_tool_results(agent) -> list[dict]:
    return _extract_tool_results_from_messages((await _get_agent_memory(agent))[-12:])


def _single_specialist_passthrough(
    tool_calls: list[dict],
    tool_results: list[dict],
) -> str | None:
    """Return specialist output directly for single-specialist turns."""
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


# The 7-step turn
async def run_turn(
    session_id: str,
    user_text: str,
    enable_critic: bool = False,
) -> dict[str, Any]:
    """Execute one user->orchestrator->specialists->reply turn."""
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

    # Step 2: build context, prepend to user message
    context = _build_context(session_id)
    trace.set_context(turn_id, context)
    trace.add_event(
        turn_id,
        "context",
        "complete",
        "Built session context snapshot",
        {"context": context},
    )

    deterministic_reply = deterministic_menu_reply(session_id, user_text)
    if deterministic_reply:
        trace.add_event(
            turn_id,
            "deterministic_menu",
            "complete",
            "Answered from canonical menu index without LLM",
            {"route": deterministic_reply.route},
        )
        trace.finish_turn(
            turn_id,
            "complete",
            deterministic_reply.reply,
            deterministic_reply.tool_calls,
            None,
        )
        return {
            "reply": deterministic_reply.reply,
            "tool_calls": deterministic_reply.tool_calls,
            "critique": None,
        }

    msg = Msg(name="user", content=f"{context} {user_text}", role="user")
    memory_before = await _get_agent_memory(orchestrator)
    memory_before_count = len(memory_before)

    # Steps 3, 4, 5: planning + execution + tool calls (handled by ReAct)
    user_request_token = set_current_user_request(user_text)
    session_token = set_current_session_id(session_id)
    try:
        trace.add_event(turn_id, "orchestrator", "running", "Calling ReAct agent")
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

    # Step 7: assemble reply
    trace.add_event(turn_id, "orchestrator", "complete", "Agent returned a reply")
    reply = _extract_text(reply_msg)
    memory_after = await _get_agent_memory(orchestrator)
    current_turn_messages = memory_after[memory_before_count:]
    if not current_turn_messages:
        current_turn_messages = memory_after[-12:]
    tool_calls = _extract_tool_calls_from_messages(current_turn_messages)
    tool_results = _extract_tool_results_from_messages(current_turn_messages)
    trace.add_event(
        turn_id,
        "tools",
        "complete",
        f"Extracted {len(tool_calls)} tool call(s)",
        {"tool_calls": tool_calls},
    )
    mutated = any(call["name"] in MUTATING_TOOLS for call in tool_calls)
    passthrough_reply = _single_specialist_passthrough(tool_calls, tool_results)
    if passthrough_reply:
        reply = passthrough_reply
        trace.add_event(
            turn_id,
            "response",
            "running",
            "Using single specialist response directly",
        )

    # Step 6: validation hook (Phase 2)
    critique_payload = None
    if enable_critic and mutated:
        # TODO Phase 2: from cafe.core.critic import critique
        # critique_payload = (await critique(user_text, reply, tool_calls)).model_dump()
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

    trace.add_event(turn_id, "response", "complete", "Final response assembled")
    trace.finish_turn(turn_id, "complete", reply, tool_calls, critique_payload)
    return {"reply": reply, "tool_calls": tool_calls, "critique": critique_payload}
