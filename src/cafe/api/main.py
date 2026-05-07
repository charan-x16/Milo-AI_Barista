"""FastAPI surface for Milo Barista. Thin - all logic is in core/."""

import logging
import time
import uuid
from contextlib import asynccontextmanager

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
)
from cafe.agents.session_manager import get_session_manager
from cafe.agents.specialist_tools import reset_specialists
from cafe.api.debug import router as debug_router
from cafe.api.schemas import ChatRequest, ChatResponse
from cafe.config import get_settings
from cafe.core.background_tasks import drain_background_tasks, session_task_key
from cafe.core.debug_trace import get_debug_trace_store
from cafe.core.startup import initialize_persistent_storage, initialize_runtime_resources
from cafe.core.state import get_store, reset_store
from cafe.core.turn_runtime import run_turn


log = logging.getLogger("cafe")


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    logging.basicConfig(level=s.log_level)
    get_store()
    initialize_runtime_resources(s)
    await initialize_persistent_storage(s)
    log.info("Milo Barista ready (provider=%s model=%s)", normalized_provider(s), s.openai_model)
    yield
    await drain_background_tasks(timeout=10.0)


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
    out = await run_turn(
        req.session_id,
        req.message,
        req.enable_critic,
        user_id=req.user_id,
    )
    return ChatResponse(user_id=req.user_id, session_id=req.session_id, **out)


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
    await drain_background_tasks(timeout=2.0)
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
    await drain_background_tasks(
        key=session_task_key(user_id, session_id),
        timeout=2.0,
    )
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
    await drain_background_tasks(
        key=session_task_key(user_id, session_id),
        timeout=5.0,
    )
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
    await drain_background_tasks(timeout=5.0)
    reset_store()
    get_store()
    reset_specialists()
    get_session_manager().reset()
    get_debug_trace_store().reset()
    return {"status": "reset"}
