"""FastAPI surface for Milo Barista. Thin - all logic is in core/."""

import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse

from cafe import __version__
from cafe.agents.llm import normalized_provider
from cafe.agents.session_manager import get_session_manager
from cafe.agents.specialist_tools import reset_specialists
from cafe.api.debug import router as debug_router
from cafe.api.schemas import ChatRequest, ChatResponse
from cafe.config import get_settings
from cafe.core.turn_runtime import run_turn
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
