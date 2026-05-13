"""FastAPI surface for Milo Barista."""

import logging
import time
import uuid
from contextlib import asynccontextmanager

from agentscope.message import Msg
from fastapi import FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse

from cafe import __version__
from cafe.agents.llm import normalized_provider
from cafe.agents.memory import (
    DEFAULT_USER_ID,
    delete_session_data,
    list_conversation_messages,
    list_user_conversations,
    maybe_generate_memory_summary,
)
from cafe.agents.session_manager import get_session_manager
from cafe.agents.specialist_tools import (
    reset_current_session_id,
    reset_current_user_request,
    reset_specialists,
    set_current_session_id,
    set_current_user_request,
)
from cafe.api.debug import router as debug_router
from cafe.api.schemas import ChatRequest, ChatResponse
from cafe.config import get_settings
from cafe.core.debug_trace import get_debug_trace_store
from cafe.core.state import get_store, reset_store


log = logging.getLogger("cafe")


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    logging.basicConfig(level=s.log_level)
    get_store()
    log.info("Milo Barista ready (provider=%s model=%s)", normalized_provider(s), s.openai_model)
    yield


app = FastAPI(title="Milo Barista", version=__version__, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(debug_router)


@app.get("/", include_in_schema=False)
async def root():
    return RedirectResponse(url="/debug/flow")


@app.middleware("http")
async def log_requests(request: Request, call_next):
    t0 = time.perf_counter()
    resp = await call_next(request)
    log.info(
        "%s %s -> %d (%.1f ms)",
        request.method,
        request.url.path,
        resp.status_code,
        (time.perf_counter() - t0) * 1000,
    )
    return resp


@app.get("/health")
async def health():
    return {"status": "ok", "version": __version__}


@app.post("/sessions")
async def new_session():
    return {"session_id": uuid.uuid4().hex}


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    trace = get_debug_trace_store()
    turn_id = trace.start_turn(req.session_id, req.message)
    trace.add_event(turn_id, "api", "running", "Chat request accepted")

    orchestrator = get_session_manager().get_or_create(
        req.session_id,
        user_id=req.user_id,
    )
    trace.add_event(
        turn_id,
        "session_manager",
        "complete",
        "Loaded SQL-backed per-session Orchestrator",
        {
            "agent_name": getattr(orchestrator, "name", "Orchestrator"),
            "user_id": req.user_id,
        },
    )

    msg = Msg(
        name="user",
        content=f"[session_id={req.session_id}] {req.message}",
        role="user",
        metadata={"display_text": req.message},
    )

    user_request_token = set_current_user_request(req.message)
    session_token = set_current_session_id(req.session_id)
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
        return ChatResponse(
            user_id=req.user_id,
            session_id=req.session_id,
            reply=reply,
            tool_calls=[],
            critique=None,
        )
    finally:
        reset_current_user_request(user_request_token)
        reset_current_session_id(session_token)

    reply = _extract_reply_text(reply_msg)
    trace.add_event(turn_id, "orchestrator", "complete", "Routing complete")

    if req.enable_critic:
        trace.add_event(
            turn_id,
            "critic",
            "skipped",
            "Critic is not wired in the simplified chat handoff",
        )

    try:
        summary = await maybe_generate_memory_summary(
            req.session_id,
            user_id=req.user_id,
        )
    except Exception as e:
        log.warning("memory summary checkpoint skipped: %s", e)
        trace.add_event(
            turn_id,
            "memory",
            "warning",
            "Memory summary checkpoint skipped",
            {"error": str(e)},
        )
    else:
        trace.add_event(
            turn_id,
            "memory",
            "complete" if summary else "skipped",
            (
                "Created checkpoint memory summary"
                if summary
                else "No memory summary checkpoint due"
            ),
        )

    trace.add_event(turn_id, "response", "complete", "Final response assembled")
    trace.finish_turn(turn_id, "complete", reply, [], None)
    return ChatResponse(
        user_id=req.user_id,
        session_id=req.session_id,
        reply=reply,
        tool_calls=[],
        critique=None,
    )


def _extract_reply_text(msg) -> str:
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


@app.get("/sessions/{session_id}/cart")
async def get_cart(session_id: str):
    cart = get_store().get_cart(session_id)
    return {
        "session_id": session_id,
        "items": [item.model_dump() for item in cart.items],
        "total_inr": cart.total_inr,
    }


@app.get("/sessions/{session_id}/orders")
async def get_orders(session_id: str):
    orders = [
        order.model_dump(mode="json")
        for order in get_store().orders.values()
        if order.session_id == session_id
    ]
    return {"session_id": session_id, "orders": orders}


@app.get("/users/{user_id}/conversations")
async def get_conversations(
    user_id: str,
    limit: int = Query(default=20, ge=1, le=100),
):
    """Frontend sidebar: recent conversations for a user."""
    return {
        "user_id": user_id,
        "conversations": await list_user_conversations(user_id=user_id, limit=limit),
    }


@app.get("/sessions/{session_id}/messages")
async def get_messages(
    session_id: str,
    user_id: str = DEFAULT_USER_ID,
    limit: int = Query(default=200, ge=1, le=500),
):
    """Frontend chat history: visible user/assistant messages for a session."""
    return {
        "user_id": user_id,
        "session_id": session_id,
        "messages": await list_conversation_messages(
            session_id=session_id,
            user_id=user_id,
            limit=limit,
        ),
    }


@app.post("/sessions/{session_id}/reset")
async def reset_session(session_id: str, user_id: str = DEFAULT_USER_ID):
    """Dev helper - clears one session from SQL memory and in-process state."""
    await delete_session_data(session_id=session_id, user_id=user_id)
    store = get_store()
    store.carts.pop(session_id, None)
    store.last_menu_scope.pop(session_id, None)
    for order_id, order in list(store.orders.items()):
        if order.session_id == session_id:
            store.orders.pop(order_id, None)
    get_session_manager().reset(session_id=session_id, user_id=user_id)
    return {"status": "reset", "user_id": user_id, "session_id": session_id}


@app.get("/menu")
async def get_menu():
    return {"items": [item.model_dump() for item in get_store().menu.values()]}


@app.post("/admin/reset")
async def admin_reset():
    """Dev only - clears carts and orders, resets specialist agents."""
    reset_store()
    get_store()
    reset_specialists()
    get_session_manager().reset()
    get_debug_trace_store().reset()
    return {"status": "reset"}
