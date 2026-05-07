"""Chat turn runtime.

The hot path is Router -> deterministic handler, with a single-call LLM
formatter only when deterministic routing misses.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from cafe.agents.memory import DEFAULT_USER_ID
from cafe.core.debug_trace import get_debug_trace_store
from cafe.core.fast_router import fast_intent_router
from cafe.core.observability import (
    TurnObserver,
    reset_current_observer,
    set_current_observer,
)
from cafe.core.single_llm import run_single_llm_fallback
from cafe.core.state import get_store


log = logging.getLogger(__name__)

_SESSION_LOCKS: dict[tuple[str, str], asyncio.Lock] = {}
_SESSION_LOCKS_GUARD = asyncio.Lock()


def _build_context(session_id: str) -> str:
    """Compact state snapshot used by router and fallback formatter."""
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


async def _get_session_lock(user_id: str, session_id: str) -> asyncio.Lock:
    """One in-process request may run per user/session at a time."""
    key = (user_id, session_id)
    async with _SESSION_LOCKS_GUARD:
        if key not in _SESSION_LOCKS:
            _SESSION_LOCKS[key] = asyncio.Lock()
        return _SESSION_LOCKS[key]


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

    context = _build_context(session_id)
    trace.set_context(turn_id, context)
    trace.add_event(
        turn_id,
        "context",
        "complete",
        "Built session context snapshot",
        {"context": context},
    )

    trace.add_event(
        turn_id,
        "fast_router",
        "running",
        "Checking deterministic fast path",
    )
    fast_result = await fast_intent_router(
        session_id=session_id,
        user_text=user_text,
        user_id=user_id,
    )
    if fast_result.matched:
        trace.add_event(
            turn_id,
            "fast_router",
            "complete",
            "Deterministic fast path handled request",
            {"intent": fast_result.intent, "route": fast_result.route},
        )
        trace.add_event(
            turn_id,
            "response",
            "complete",
            "Final response assembled",
            {"source": "fast_router"},
        )
        trace.finish_turn(turn_id, "complete", fast_result.reply, [], None)
        observer.set_result(
            status="complete",
            reply_source="fast_router",
            tool_calls=[],
            intent=fast_result.intent,
            fallback_path=fast_result.route,
        )
        return {
            "request_id": observer.request_id,
            "reply": fast_result.reply,
            "tool_calls": fast_result.tool_calls,
            "critique": None,
        }

    trace.add_event(
        turn_id,
        "fast_router",
        "skipped",
        "No deterministic route matched; using single LLM fallback",
    )

    try:
        trace.add_event(
            turn_id,
            "single_llm",
            "running",
            "Formatting complex fallback request",
        )
        reply = await run_single_llm_fallback(
            session_id=session_id,
            user_id=user_id,
            user_text=user_text,
            session_context=context,
        )
    except Exception as e:
        log.exception("single LLM fallback failed")
        reply = f"Sorry, something went wrong: {e}"
        trace.add_event(
            turn_id,
            "single_llm",
            "error",
            "Single LLM fallback raised an exception",
            {"error": str(e)},
        )
        trace.finish_turn(turn_id, "error", reply, [], None)
        observer.set_result(
            status="error",
            reply_source="single_llm",
            tool_calls=[],
            intent="complex_fallback",
            fallback_path="fast_router->single_llm",
            error=str(e),
        )
        return {
            "request_id": observer.request_id,
            "reply": reply,
            "tool_calls": [],
            "critique": None,
        }

    critique_payload = None
    trace.add_event(
        turn_id,
        "critic",
        "skipped",
        "Critic skipped for single-call fallback",
        {"enable_critic": enable_critic, "mutated": False},
    )

    trace.add_event(
        turn_id,
        "response",
        "complete",
        "Final response assembled",
        {"source": "single_llm"},
    )

    log.info("Final response source=%s preview=%r", "single_llm", reply[:160])
    trace.finish_turn(turn_id, "complete", reply, [], critique_payload)
    observer.set_result(
        status="complete",
        reply_source="single_llm",
        tool_calls=[],
        intent="complex_fallback",
        fallback_path="fast_router->single_llm",
    )
    return {
        "request_id": observer.request_id,
        "reply": reply,
        "tool_calls": [],
        "critique": critique_payload,
    }
