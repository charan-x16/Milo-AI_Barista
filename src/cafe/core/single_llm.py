"""Single-call LLM fallback for complex requests.

This is the only LLM path used by /chat after deterministic routing misses.
It does not expose tools, specialists, or recursive agent loops.
"""

from __future__ import annotations

from typing import Any

from agentscope.message import Msg

from cafe.agents.llm import make_chat_model
from cafe.agents.memory import (
    get_recent_messages,
    get_summary,
    load_memory,
    make_chat_formatter,
    save_messages,
)
from cafe.config import get_settings
from cafe.core.background_tasks import (
    drain_background_tasks,
    schedule_background,
    session_task_key,
)
from cafe.core.observability import observed_span


SINGLE_LLM_PROMPT = """You are Milo at By The Brew.

You are the fallback formatter for complex chat requests after deterministic
menu/cart/order/support routing has already been attempted. Keep replies short,
human, and useful.

Rules:
- Do not call tools.
- Do not invent order, cart, live offer, or menu facts.
- For menu, cart, order, timings, offers, or FAQ actions, tell the customer the
  exact action you can help with and ask for the missing detail.
- For general cafe conversation, answer naturally in Milo's voice.
"""


async def run_single_llm_fallback(
    *,
    session_id: str,
    user_id: str,
    user_text: str,
    session_context: str,
) -> str:
    """Return one optional LLM-formatted answer and persist the visible turn."""
    await drain_background_tasks(
        key=session_task_key(user_id, session_id),
        timeout=5.0,
    )
    with observed_span("llm_fallback", "single_llm.prepare"):
        memory = load_memory(session_id, user_id=user_id)
        messages = [Msg("system", SINGLE_LLM_PROMPT, "system")]
        if summary := await get_summary(memory):
            messages.append(summary)
        messages.extend(await get_recent_messages(memory))
        messages.append(
            Msg(
                "user",
                f"{session_context} {user_text}",
                "user",
                metadata={"display_text": user_text},
            )
        )

    try:
        settings = get_settings()
        formatter = make_chat_formatter(settings)
        model = make_chat_model(settings, agent_name="SingleLLMFallback")
        formatted = await formatter.format(messages)
        response = await model(formatted, tools=None, tool_choice="none")
        reply = _extract_chat_response_text(response).strip()
    except Exception:
        reply = _deterministic_fallback_reply()

    reply = reply or _deterministic_fallback_reply()
    schedule_background(
        save_messages(
            memory,
            [
                Msg(
                    "user",
                    f"[session_id={session_id}] {user_text}",
                    "user",
                    metadata={"display_text": user_text},
                ),
                Msg("assistant", reply, "assistant"),
            ],
        ),
        name="save_single_llm_turn",
        key=session_task_key(user_id, session_id),
    )
    return reply


def _extract_chat_response_text(response: Any) -> str:
    content = getattr(response, "content", "") or ""
    if isinstance(content, str):
        return content

    parts: list[str] = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(str(block.get("text", "")))
        elif getattr(block, "type", None) == "text":
            parts.append(str(getattr(block, "text", "")))
    return "".join(parts)


def _deterministic_fallback_reply() -> str:
    return (
        "I can help with the menu, cart, orders, timings, offers, and cafe FAQs. "
        "Could you share what you would like to do next?"
    )
