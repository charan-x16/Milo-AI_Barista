"""Developer dashboard for visualizing the runtime agent flow."""

from __future__ import annotations

import asyncio
import json
from typing import AsyncIterator

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, StreamingResponse

from cafe.agents.llm import normalized_provider
from cafe.agents.session_manager import get_session_manager
from cafe.api.debug_dashboard import DASHBOARD_HTML as DASHBOARD_PAGE_HTML
from cafe.config import get_settings
from cafe.core.debug_trace import get_debug_trace_store
from cafe.core.state import get_store

router = APIRouter(prefix="/debug", tags=["debug"])


@router.get("/flow", response_class=HTMLResponse)
async def flow_dashboard() -> HTMLResponse:
    """Handle flow dashboard.

    Returns:
        - return HTMLResponse - The return value.
    """
    return HTMLResponse(DASHBOARD_PAGE_HTML)


@router.get("/flow/state")
async def flow_state() -> dict:
    """Handle flow state.

    Returns:
        - return dict - The return value.
    """
    return build_flow_state()


@router.get("/flow/events")
async def flow_events() -> StreamingResponse:
    """Handle flow events.

    Returns:
        - return StreamingResponse - The return value.
    """

    async def stream() -> AsyncIterator[str]:
        """Handle stream.

        Returns:
            - return AsyncIterator[str] - The return value.
        """
        last_version = -1
        while True:
            state = build_flow_state()
            if state["version"] != last_version:
                last_version = state["version"]
                payload = json.dumps(state, ensure_ascii=False)
                yield f"data: {payload}\n\n"
            await asyncio.sleep(1)

    return StreamingResponse(stream(), media_type="text/event-stream")


def build_flow_state() -> dict:
    """Build the flow state.

    Returns:
        - return dict - The return value.
    """
    trace = get_debug_trace_store().snapshot()
    store = get_store()
    session_ids = get_session_manager().session_ids()
    settings = get_settings()

    carts = [
        {
            "session_id": session_id,
            "items": len(cart.items),
            "total_inr": cart.total_inr,
        }
        for session_id, cart in sorted(store.carts.items())
    ]
    orders = [
        {
            "order_id": order.order_id,
            "session_id": order.session_id,
            "status": order.status,
            "total_inr": order.total_inr,
        }
        for order in store.orders.values()
    ]

    return {
        **trace,
        "components": [
            {"name": "FastAPI", "status": "online"},
            {"name": "SessionManager", "status": f"{len(session_ids)} active"},
            {
                "name": "StateStore",
                "status": f"{len(carts)} cart(s), {len(orders)} order(s)",
            },
            {"name": "IntentRouter", "status": "deterministic hot path"},
            {"name": "Orchestrator", "status": "agentic dispatcher"},
            {"name": "Specialists", "status": "tool-owning answer agents"},
            {
                "name": "Memory",
                "status": (
                    f"summary checkpoint every "
                    f"{settings.memory_summary_checkpoint_messages} messages; "
                    "no inline compression"
                ),
            },
        ],
        "runtime": {
            "provider": normalized_provider(settings),
            "model": settings.openai_model,
            "active_sessions": session_ids,
            "memory_max_prompt_tokens": settings.memory_max_prompt_tokens,
            "memory_summary_checkpoint_messages": (
                settings.memory_summary_checkpoint_messages
            ),
            "memory_keep_recent_messages": settings.memory_keep_recent_messages,
        },
        "state": {
            "carts": carts,
            "orders": orders[-12:],
            "menu_items": len(store.menu),
        },
    }
