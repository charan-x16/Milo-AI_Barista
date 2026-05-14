"""FastAPI surface for Milo Barista."""

import asyncio
import json
import logging
import time
import uuid
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from agentscope.message import Msg
from fastapi import BackgroundTasks, FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse, StreamingResponse

from cafe import __version__
from cafe.agents.llm import normalized_provider
from cafe.agents.memory import (
    DEFAULT_USER_ID,
    delete_session_data,
    list_conversation_messages,
    list_user_conversations,
    maybe_generate_memory_summary,
)
from cafe.agents.agent_cache import initialize_agent_cache, is_agent_cache_ready
from cafe.agents.session_manager import get_session_manager
from cafe.agents.specialist_tools import (
    reset_current_session_id,
    reset_current_user_id,
    reset_current_user_request,
    reset_specialists,
    set_current_session_id,
    set_current_user_id,
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
    log.info("Application startup - initializing specialist agent cache...")
    try:
        initialize_agent_cache()
    except RuntimeError as e:
        if "LLM_API_KEY not set" not in str(e):
            raise
        log.warning("Specialist agent cache initialization skipped: %s", e)
    else:
        log.info("Specialist agent cache ready")
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
    return {
        "status": "ok",
        "version": __version__,
        "agent_cache_ready": is_agent_cache_ready(),
        "timestamp": time.time(),
    }


@app.post("/sessions")
async def new_session():
    return {"session_id": uuid.uuid4().hex}


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest, background_tasks: BackgroundTasks):
    session_id = req.session_id or uuid.uuid4().hex
    trace = get_debug_trace_store()
    turn_id = trace.start_turn(session_id, req.message)
    trace.add_event(turn_id, "api", "running", "Chat request accepted")

    orchestrator = get_session_manager().get_or_create(
        session_id,
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
        content=f"[session_id={session_id}] {req.message}",
        role="user",
        metadata={"display_text": req.message},
    )

    user_request_token = set_current_user_request(req.message)
    session_token = set_current_session_id(session_id)
    user_id_token = set_current_user_id(req.user_id)
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
            session_id=session_id,
            reply=reply,
            tool_calls=[],
            critique=None,
        )
    finally:
        reset_current_user_request(user_request_token)
        reset_current_session_id(session_token)
        reset_current_user_id(user_id_token)

    reply = _extract_reply_text(reply_msg)
    trace.add_event(turn_id, "orchestrator", "complete", "Routing complete")

    if req.enable_critic:
        trace.add_event(
            turn_id,
            "critic",
            "skipped",
            "Critic is not wired in the simplified chat handoff",
        )

    trace.add_event(turn_id, "response", "complete", "Final response assembled")
    trace.finish_turn(turn_id, "complete", reply, [], None)
    trace.add_event(
        turn_id,
        "memory",
        "scheduled",
        "Memory summary checkpoint scheduled in background",
    )
    log.info("[%s] Returning response at %.3f", session_id, time.time())
    background_tasks.add_task(
        maybe_generate_memory_summary_safe,
        session_id,
        req.user_id,
        turn_id,
    )
    return ChatResponse(
        user_id=req.user_id,
        session_id=session_id,
        reply=reply,
        tool_calls=[],
        critique=None,
    )


def _sse_event(event_type: str, **payload) -> str:
    data = {"type": event_type, **payload}
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


async def _stream_reply_chunks(reply: str) -> AsyncGenerator[str, None]:
    words = reply.split()
    if not words:
        return

    chunk_size = 4
    for index in range(0, len(words), chunk_size):
        chunk = " ".join(words[index : index + chunk_size])
        if index + chunk_size < len(words):
            chunk += " "
        yield _sse_event("content", content=chunk)
        await asyncio.sleep(0.03)


async def _chat_stream_events(req: ChatRequest) -> AsyncGenerator[str, None]:
    """Yield SSE events for one chat turn."""
    trace = get_debug_trace_store()
    session_id = req.session_id or uuid.uuid4().hex
    user_id = req.user_id or DEFAULT_USER_ID
    turn_id = trace.start_turn(session_id, req.message)
    trace.add_event(turn_id, "api", "running", "Streaming chat request accepted")

    user_request_token = None
    session_token = None
    user_id_token = None

    yield _sse_event(
        "status",
        content="Routing your request...",
        session_id=session_id,
    )

    try:
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

        msg = Msg(
            name="user",
            content=f"[session_id={session_id}] {req.message}",
            role="user",
            metadata={"display_text": req.message},
        )

        user_request_token = set_current_user_request(req.message)
        session_token = set_current_session_id(session_id)
        user_id_token = set_current_user_id(user_id)

        trace.add_event(turn_id, "orchestrator", "running", "Routing request")
        reply_msg = await orchestrator(msg)
        reply = _extract_reply_text(reply_msg)
        trace.add_event(turn_id, "orchestrator", "complete", "Routing complete")

        if req.enable_critic:
            trace.add_event(
                turn_id,
                "critic",
                "skipped",
                "Critic is not wired in the simplified chat handoff",
            )

        trace.add_event(turn_id, "response", "running", "Streaming final response")
        async for chunk in _stream_reply_chunks(reply):
            yield chunk

        trace.add_event(turn_id, "response", "complete", "Final response streamed")
        trace.finish_turn(turn_id, "complete", reply, [], None)
        yield _sse_event("done", session_id=session_id)

        trace.add_event(
            turn_id,
            "memory",
            "scheduled",
            "Memory summary checkpoint scheduled in background",
        )
        log.info("[%s] Stream completed at %.3f", session_id, time.time())
        asyncio.create_task(
            maybe_generate_memory_summary_safe(session_id, user_id, turn_id)
        )

    except Exception as e:
        log.exception("[%s] Chat stream error: %s", session_id, e)
        trace.add_event(
            turn_id,
            "orchestrator",
            "error",
            "Streaming chat raised an exception",
            {"error": str(e)},
        )
        trace.finish_turn(turn_id, "error", str(e), [], None)
        yield _sse_event("error", content=str(e), session_id=session_id)

    finally:
        if user_request_token is not None:
            reset_current_user_request(user_request_token)
        if session_token is not None:
            reset_current_session_id(session_token)
        if user_id_token is not None:
            reset_current_user_id(user_id_token)


@app.post("/chat/stream")
async def chat_stream(req: ChatRequest) -> StreamingResponse:
    """Streaming chat endpoint using Server-Sent Events."""
    return StreamingResponse(
        _chat_stream_events(req),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


async def maybe_generate_memory_summary_safe(
    session_id: str,
    user_id: str = DEFAULT_USER_ID,
    turn_id: int | None = None,
) -> None:
    """Run checkpoint memory summaries outside the user-facing response path."""
    trace = get_debug_trace_store()
    started_at = time.time()
    log.info("[%s] Memory summary started at %.3f", session_id, started_at)
    if turn_id is not None:
        trace.add_event(
            turn_id,
            "memory",
            "running",
            "Memory summary background task started",
        )

    try:
        summary = await maybe_generate_memory_summary(session_id, user_id=user_id)
    except Exception as e:
        log.exception("[%s] Memory summary failed: %s", session_id, e)
        if turn_id is not None:
            trace.add_event(
                turn_id,
                "memory",
                "warning",
                "Memory summary background task failed",
                {"error": str(e)},
            )
        return

    completed_at = time.time()
    log.info(
        "[%s] Memory summary completed at %.3f (created=%s)",
        session_id,
        completed_at,
        bool(summary),
    )
    if turn_id is not None:
        trace.add_event(
            turn_id,
            "memory",
            "complete" if summary else "skipped",
            (
                "Created checkpoint memory summary in background"
                if summary
                else "No memory summary checkpoint due"
            ),
            {
                "started_at": started_at,
                "completed_at": completed_at,
            },
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
