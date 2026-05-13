"""Checkpoint summary orchestration helpers."""

from __future__ import annotations

import json
import re
from hashlib import sha256
from typing import Any

from agentscope.message import Msg

from cafe.config import Settings, get_settings

from .models import (
    MEMORY_SUMMARY_OVERLAP_MESSAGES,
    MemorySummaryDraft,
    MemorySummaryInsert,
    MemorySummarizer,
)
from .prompts import MEMORY_SUMMARY_PROMPT
from .repositories import MemorySummaryRepository


DEFAULT_USER_ID = "anonymous"


def source_start_for_checkpoint(
    visible_count: int,
    interval: int,
    previous_summary: dict[str, Any] | None,
) -> int:
    if previous_summary is None:
        return 1
    return max(1, visible_count - interval - MEMORY_SUMMARY_OVERLAP_MESSAGES + 1)


def source_messages_from_rows(
    rows: list[dict[str, Any]],
    source_start: int,
) -> list[dict[str, Any]]:
    return [
        {
            "ordinal": source_start + index,
            "role": row["role"],
            "name": row["name"],
            "content": row["compact_content"] or "",
            "sequence_no": row["sequence_no"],
        }
        for index, row in enumerate(rows)
    ]


def render_summary_input(
    previous_summary: str | None,
    source_messages: list[dict[str, Any]],
) -> str:
    messages_text = "\n".join(
        (
            f"[{msg['ordinal']}] {msg['role']} {msg['name']}: "
            f"{msg['content']}"
        )
        for msg in source_messages
    )
    return (
        "Previous cumulative summary:\n"
        f"{previous_summary or '(none)'}\n\n"
        "Visible messages to fold in:\n"
        f"{messages_text or '(none)'}"
    )


def parse_summary_json(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError:
        return {
            "summary_text": _truncate(text, 3000),
            "preferences": [],
            "decisions": [],
            "important_facts": [],
            "cart_order_context": [],
            "unresolved_questions": [],
        }
    return payload if isinstance(payload, dict) else {"summary_text": str(payload)}


async def get_latest_memory_summary(
    session_id: str,
    user_id: str = DEFAULT_USER_ID,
    *,
    settings: Settings | None = None,
) -> dict[str, Any] | None:
    """Return the latest active cumulative memory summary for one session."""
    from cafe.agents.memory.storage import (
        CONVERSATION_MESSAGES_TABLE,
        MEMORY_SUMMARIES_TABLE,
        load_memory,
    )

    memory = load_memory(session_id, user_id=user_id, settings=settings)
    await memory._create_table()
    repo = MemorySummaryRepository(
        memory.engine,
        summary_table=MEMORY_SUMMARIES_TABLE,
        message_table=CONVERSATION_MESSAGES_TABLE,
    )
    return await repo.latest_summary(memory.conversation_id)


async def maybe_generate_memory_summary(
    session_id: str,
    user_id: str = DEFAULT_USER_ID,
    *,
    settings: Settings | None = None,
    summarizer: MemorySummarizer | None = None,
) -> dict[str, Any] | None:
    """Generate a cumulative summary at visible-message checkpoints."""
    from cafe.agents.memory.storage import (
        CONVERSATION_MESSAGES_TABLE,
        MEMORY_SUMMARIES_TABLE,
        load_memory,
    )

    settings = settings or get_settings()
    interval = max(settings.memory_summary_interval_messages, 1)
    memory = load_memory(session_id, user_id=user_id, settings=settings)
    await memory._create_table()
    repo = MemorySummaryRepository(
        memory.engine,
        summary_table=MEMORY_SUMMARIES_TABLE,
        message_table=CONVERSATION_MESSAGES_TABLE,
    )

    visible_rows = await repo.visible_message_rows(memory.conversation_id)
    visible_count = len(visible_rows)
    if visible_count == 0 or visible_count % interval != 0:
        return None
    if await repo.checkpoint_exists(memory.conversation_id, visible_count):
        return None

    previous_summary = await repo.latest_summary(memory.conversation_id)
    source_start = source_start_for_checkpoint(
        visible_count,
        interval,
        previous_summary,
    )
    source_end = visible_count
    source_rows = visible_rows[source_start - 1 : source_end]
    source_messages = source_messages_from_rows(source_rows, source_start)

    generate = summarizer or make_llm_memory_summarizer(settings)
    draft = await generate(
        previous_summary["summary_text"] if previous_summary else None,
        source_messages,
    )
    summary_text = draft.summary_text.strip()
    if not summary_text:
        return None

    next_version = int(previous_summary["summary_version"]) + 1 if previous_summary else 1
    record = MemorySummaryInsert(
        id=sha256(
            (
                f"{memory.conversation_id}:memory-summary:"
                f"{next_version}:{visible_count}"
            ).encode("utf-8")
        ).hexdigest(),
        conversation_id=memory.conversation_id,
        user_id=user_id,
        summary_version=next_version,
        checkpoint_message_count=visible_count,
        source_message_start=source_start,
        source_message_end=source_end,
        previous_summary_id=previous_summary["id"] if previous_summary else None,
        summary_text=summary_text,
        summary_json=draft.summary_json if isinstance(draft.summary_json, dict) else {},
        metadata={
            "kind": "checkpoint_summary",
            "interval": interval,
            "overlap_messages": MEMORY_SUMMARY_OVERLAP_MESSAGES,
        },
    )

    async with memory._lock:
        inserted = await repo.insert_summary(record)
        if not inserted:
            return None

    memory._compressed_summary = summary_text
    memory._summary_hydrated = True
    return await repo.latest_summary(memory.conversation_id)


def make_llm_memory_summarizer(settings: Settings) -> MemorySummarizer:
    async def summarize(
        previous_summary: str | None,
        source_messages: list[dict[str, Any]],
    ) -> MemorySummaryDraft:
        from agentscope.agent import ReActAgent
        from agentscope.memory import InMemoryMemory

        from cafe.agents.llm import make_chat_model
        from cafe.agents.memory import make_chat_formatter

        agent = ReActAgent(
            name="MemorySummaryAgent",
            sys_prompt=MEMORY_SUMMARY_PROMPT,
            model=make_chat_model(settings),
            formatter=make_chat_formatter(settings),
            memory=InMemoryMemory(),
            max_iters=1,
        )
        reply = await agent(
            Msg(
                "user",
                render_summary_input(previous_summary, source_messages),
                "user",
            )
        )
        text = _content_text(getattr(reply, "content", ""))
        payload = parse_summary_json(text)
        summary_text = str(payload.get("summary_text") or "").strip()
        if not summary_text:
            summary_text = _truncate(text, 3000)
        payload["summary_text"] = summary_text
        return MemorySummaryDraft(summary_text=summary_text, summary_json=payload)

    return summarize


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content

    parts: list[str] = []
    for block in content or []:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(str(block.get("text", "")))
        elif getattr(block, "type", None) == "text":
            parts.append(str(getattr(block, "text", "")))
        else:
            parts.append(str(block))
    return "\n".join(part for part in parts if part)


def _truncate(text: str, limit: int) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return f"{text[: limit - 3]}..."
