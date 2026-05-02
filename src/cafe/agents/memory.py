"""Persistent short-term memory for AgentScope chat turns.

AgentScope owns the ReAct loop, but this module owns the production memory
contract: SQL persistence, per-request context windowing, compact tool results,
summary persistence, and helper functions for the agent runtime.
"""

from __future__ import annotations

import json
from hashlib import sha256
from copy import deepcopy
from inspect import currentframe
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from pydantic import BaseModel, Field

from agentscope.agent import ReActAgent
from agentscope.formatter import (
    AnthropicChatFormatter,
    AnthropicMultiAgentFormatter,
    DashScopeChatFormatter,
    DashScopeMultiAgentFormatter,
    DeepSeekChatFormatter,
    DeepSeekMultiAgentFormatter,
    GeminiChatFormatter,
    GeminiMultiAgentFormatter,
    OllamaChatFormatter,
    OllamaMultiAgentFormatter,
    OpenAIChatFormatter,
    OpenAIMultiAgentFormatter,
)
from agentscope.memory import AsyncSQLAlchemyMemory
from agentscope.message import Msg, TextBlock
from agentscope.token import CharTokenCounter, OpenAITokenCounter

from cafe.agents.llm import normalized_provider
from cafe.config import Settings, get_settings


DEFAULT_USER_ID = "anonymous"
SUMMARY_MARK = "summary"
TOOL_CALL_MARK = "tool_call"
TOOL_RESULT_MARK = "tool_result"
COMPRESSED_MARK = "compressed"
TOOL_RESULT_MAX_CHARS = 2000

_ENGINE: AsyncEngine | None = None


class CafeConversationSummary(BaseModel):
    """What older chat turns should preserve after compression."""

    key_user_decisions: str = Field(
        max_length=400,
        description="Confirmed user choices and decisions made in the session.",
    )
    important_facts: str = Field(
        max_length=450,
        description="Order details, cart state, preferences, constraints, and facts.",
    )
    unresolved_questions: str = Field(
        max_length=350,
        description="Open questions, confirmations, blockers, or next actions.",
    )


SUMMARY_TEMPLATE = (
    "<conversation_summary>"
    "Key user decisions: {key_user_decisions}\n"
    "Important facts: {important_facts}\n"
    "Unresolved questions: {unresolved_questions}"
    "</conversation_summary>"
)

COMPRESSION_PROMPT = (
    "<system-hint>Summarize the older cafe conversation so the assistant can "
    "continue naturally. Preserve key user decisions, order details, session "
    "preferences, important facts, unresolved questions, and promises made. "
    "Keep the recent messages out of the summary because they remain visible."
    "</system-hint>"
)


def _window_size(settings: Settings) -> int:
    return min(max(settings.memory_keep_recent_messages, 8), 12)


def _storage_session_id(user_id: str, session_id: str) -> str:
    return sha256(f"{user_id}\0{session_id}".encode("utf-8")).hexdigest()


def _ensure_sqlite_parent(database_url: str) -> None:
    prefix = "sqlite+aiosqlite:///"
    if not database_url.startswith(prefix):
        return

    path = database_url.removeprefix(prefix)
    if path.startswith(":memory:"):
        return

    db_path = Path(path)
    if not db_path.is_absolute():
        db_path = Path.cwd() / db_path
    db_path.parent.mkdir(parents=True, exist_ok=True)


def _get_engine(settings: Settings) -> AsyncEngine:
    global _ENGINE
    if _ENGINE is None:
        _ensure_sqlite_parent(settings.memory_database_url)
        _ENGINE = create_async_engine(
            settings.memory_database_url,
            pool_pre_ping=True,
        )
    return _ENGINE


def _block_type(block: Any) -> str | None:
    if isinstance(block, dict):
        return block.get("type")
    return getattr(block, "type", None)


def _block_id(block: Any) -> str | None:
    if isinstance(block, dict):
        return block.get("id")
    return getattr(block, "id", None)


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


def _truncate(text: str, limit: int = TOOL_RESULT_MAX_CHARS) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return f"{text[: limit - 3]}..."


def _summarize_json_payload(payload: dict[str, Any]) -> str:
    if payload.get("success") is False:
        return f"Tool failed: {payload.get('error') or 'unknown error'}"

    data = payload.get("data")
    if not isinstance(data, dict):
        return _truncate(json.dumps(payload, ensure_ascii=False, default=str))

    if display_text := data.get("display_text"):
        return _truncate(str(display_text))

    if answer := data.get("answer"):
        topic = data.get("topic")
        return _truncate(f"{topic}: {answer}" if topic else str(answer))

    if cart := data.get("cart"):
        items = cart.get("items", []) if isinstance(cart, dict) else []
        total = data.get("total_inr") or cart.get("total_inr")
        names = [
            str(item.get("name") or item.get("item_id"))
            for item in items[:5]
            if isinstance(item, dict)
        ]
        return _truncate(
            f"Cart has {data.get('item_count', len(items))} item(s), "
            f"total INR {total}. Items: {', '.join(names) or 'none'}."
        )

    if order := data.get("order"):
        if isinstance(order, dict):
            return _truncate(
                "Order "
                f"{order.get('order_id', 'unknown')} is "
                f"{order.get('status', 'unknown')} with total INR "
                f"{order.get('total_inr', 'unknown')}."
            )

    for key in ("items", "results", "menu_results", "attribute_results"):
        values = data.get(key)
        if isinstance(values, list):
            count = data.get("count") or data.get(f"{key.removesuffix('s')}_count")
            names = []
            for item in values[:5]:
                if isinstance(item, dict):
                    names.append(str(item.get("name") or item.get("text") or item))
                else:
                    names.append(str(item))
            return _truncate(
                f"{key}: {count or len(values)} result(s). "
                f"Top entries: {'; '.join(names)}"
            )

    return _truncate(json.dumps(data, ensure_ascii=False, default=str))


def _summarize_tool_output(output: Any) -> str:
    text = _content_text(output)
    try:
        parsed = json.loads(text)
    except (TypeError, json.JSONDecodeError):
        return _truncate(text)

    if isinstance(parsed, dict):
        return _summarize_json_payload(parsed)
    return _truncate(json.dumps(parsed, ensure_ascii=False, default=str))


def _compact_tool_result_msg(msg: Msg) -> Msg:
    """Store readable tool-result summaries, not large raw tool payloads."""
    if not msg.has_content_blocks("tool_result"):
        return msg

    copied = Msg.from_dict(deepcopy(msg.to_dict()))
    copied.invocation_id = msg.invocation_id
    if isinstance(copied.content, str):
        return copied

    compacted = []
    for block in copied.content:
        if _block_type(block) != "tool_result":
            compacted.append(block)
            continue

        new_block = deepcopy(block)
        if isinstance(new_block, dict):
            new_block["output"] = [
                TextBlock(
                    type="text",
                    text=_summarize_tool_output(new_block.get("output")),
                )
            ]
        else:
            new_block.output = [
                TextBlock(
                    type="text",
                    text=_summarize_tool_output(getattr(new_block, "output", None)),
                )
            ]
        compacted.append(new_block)

    copied.content = compacted
    return copied


def _split_mixed_tool_messages(msg: Msg) -> list[Msg]:
    """Keep tool calls and tool results in separate Msg records."""
    if not (
        msg.has_content_blocks("tool_use")
        and msg.has_content_blocks("tool_result")
        and isinstance(msg.content, list)
    ):
        return [msg]

    tool_calls = [b for b in msg.content if _block_type(b) == "tool_use"]
    tool_results = [b for b in msg.content if _block_type(b) == "tool_result"]
    other_blocks = [
        b for b in msg.content if _block_type(b) not in {"tool_use", "tool_result"}
    ]

    messages: list[Msg] = []
    if other_blocks or tool_calls:
        call_msg = Msg(
            name=msg.name,
            content=[*other_blocks, *tool_calls],
            role=msg.role,
            metadata=deepcopy(msg.metadata),
            timestamp=msg.timestamp,
            invocation_id=msg.invocation_id,
        )
        call_msg.id = msg.id
        messages.append(call_msg)

    if tool_results:
        messages.append(
            Msg(
                name="system",
                content=tool_results,
                role="system",
                metadata=deepcopy(msg.metadata),
                invocation_id=msg.invocation_id,
            )
        )
    return messages


def _called_from_compression() -> bool:
    frame = currentframe()
    while frame:
        if frame.f_code.co_name == "_compress_memory_if_needed":
            return True
        frame = frame.f_back
    return False


def _expand_start_for_tool_pairs(msgs: list[Msg], start: int) -> int:
    """Move the window start back if it would orphan a tool_result."""
    while start > 0:
        selected = msgs[start:]
        selected_call_ids = {
            _block_id(block)
            for msg in selected
            for block in msg.get_content_blocks("tool_use")
        }
        selected_result_ids = {
            _block_id(block)
            for msg in selected
            for block in msg.get_content_blocks("tool_result")
        }
        missing_call_ids = selected_result_ids - selected_call_ids - {None}
        if not missing_call_ids:
            return start

        moved = False
        for idx in range(start - 1, -1, -1):
            call_ids = {
                _block_id(block)
                for block in msgs[idx].get_content_blocks("tool_use")
            }
            if call_ids & missing_call_ids:
                start = idx
                moved = True
                break
        if not moved:
            return start
    return start


def _recent_window(msgs: list[Msg], keep_recent: int) -> list[Msg]:
    if len(msgs) <= keep_recent:
        return msgs
    start = max(0, len(msgs) - keep_recent)
    return msgs[_expand_start_for_tool_pairs(msgs, start) :]


class PersistentSessionMemory(AsyncSQLAlchemyMemory):
    """SQL memory with durable summaries and bounded prompt context."""

    def __init__(
        self,
        engine: AsyncEngine,
        *,
        user_id: str,
        session_id: str,
        keep_recent: int,
    ) -> None:
        super().__init__(
            engine,
            session_id=_storage_session_id(user_id, session_id),
            user_id=user_id,
        )
        self.logical_session_id = session_id
        self.keep_recent = keep_recent
        self._summary_hydrated = False

    async def _hydrate_summary(self) -> None:
        if self._summary_hydrated:
            return
        summaries = await super().get_memory(
            mark=SUMMARY_MARK,
            prepend_summary=False,
        )
        self._compressed_summary = (
            summaries[-1].get_text_content() if summaries else ""
        ) or ""
        self._summary_hydrated = True

    async def update_compressed_summary(self, summary: str) -> None:
        # AgentScope keeps the active summary on the memory object; we also
        # store it as a marked Msg so process restarts can reload it.
        await super().update_compressed_summary(summary)
        await self.delete_by_mark(SUMMARY_MARK)
        await self.add(
            Msg("memory", summary, "user", metadata={"kind": SUMMARY_MARK}),
            marks=[SUMMARY_MARK, COMPRESSED_MARK],
            skip_duplicated=False,
        )
        self._summary_hydrated = True

    async def clear(self) -> None:
        await self._create_table()
        await super().clear()

    async def delete_by_mark(self, mark: str | list[str], **kwargs: Any) -> int:
        await self._create_table()
        return await super().delete_by_mark(mark, **kwargs)

    async def get_uncompressed_messages(self) -> list[Msg]:
        """Return full uncompressed DB messages, excluding the summary marker."""
        await self._hydrate_summary()
        msgs = await super().get_memory(
            exclude_mark=COMPRESSED_MARK,
            prepend_summary=False,
        )
        return [m for m in msgs if m.metadata.get("kind") != SUMMARY_MARK]

    async def get_memory(
        self,
        mark: str | None = None,
        exclude_mark: str | None = None,
        prepend_summary: bool = True,
        **kwargs: Any,
    ) -> list[Msg]:
        await self._hydrate_summary()

        # Compression needs the full uncompressed set to decide what to mark.
        # Normal ReAct prompt construction gets only summary + recent window.
        if exclude_mark == COMPRESSED_MARK and not _called_from_compression():
            msgs = await super().get_memory(
                mark=mark,
                exclude_mark=exclude_mark,
                prepend_summary=False,
                **kwargs,
            )
            msgs = [m for m in msgs if m.metadata.get("kind") != SUMMARY_MARK]

            current = None
            if msgs and msgs[-1].role == "user":
                current = msgs[-1]
                msgs = msgs[:-1]

            recent = _recent_window(msgs, self.keep_recent)
            if current is not None:
                recent = [*recent, current]

            if prepend_summary and self._compressed_summary:
                return [Msg("memory", self._compressed_summary, "user"), *recent]
            return recent

        return await super().get_memory(
            mark=mark,
            exclude_mark=exclude_mark,
            prepend_summary=prepend_summary,
            **kwargs,
        )

    async def add(
        self,
        memories: Msg | list[Msg] | None,
        marks: str | list[str] | None = None,
        **kwargs: Any,
    ) -> None:
        if memories is None:
            return

        if isinstance(memories, Msg):
            memories = [memories]

        base_marks = [marks] if isinstance(marks, str) else list(marks or [])
        for original in memories:
            for msg in _split_mixed_tool_messages(original):
                msg = _compact_tool_result_msg(msg)
                msg_marks = list(base_marks)
                if msg.has_content_blocks("tool_use"):
                    msg_marks.append(TOOL_CALL_MARK)
                if msg.has_content_blocks("tool_result"):
                    msg_marks.append(TOOL_RESULT_MARK)
                await super().add(
                    msg,
                    marks=sorted(set(msg_marks)) or None,
                    skip_duplicated=kwargs.get("skip_duplicated", True),
                )


def load_memory(
    session_id: str,
    user_id: str = DEFAULT_USER_ID,
    settings: Settings | None = None,
) -> PersistentSessionMemory:
    """Create SQL-backed memory keyed by user_id and session_id."""
    settings = settings or get_settings()
    return PersistentSessionMemory(
        _get_engine(settings),
        user_id=user_id,
        session_id=session_id,
        keep_recent=_window_size(settings),
    )


async def get_summary(memory: AsyncSQLAlchemyMemory) -> Msg | None:
    summaries = await memory.get_memory(mark=SUMMARY_MARK, prepend_summary=False)
    return summaries[-1] if summaries else None


async def get_recent_messages(memory: AsyncSQLAlchemyMemory) -> list[Msg]:
    return await memory.get_memory(
        exclude_mark=COMPRESSED_MARK,
        prepend_summary=False,
    )


async def build_context(memory: AsyncSQLAlchemyMemory) -> list[Msg]:
    """Return prompt messages in the required order before the current input."""
    from cafe.agents.prompts import ORCHESTRATOR_PROMPT

    msgs = [Msg("system", ORCHESTRATOR_PROMPT, "system")]
    if summary := await get_summary(memory):
        msgs.append(summary)
    msgs.extend(await get_recent_messages(memory))
    return msgs


async def save_messages(
    memory: AsyncSQLAlchemyMemory,
    msgs: Msg | list[Msg],
    marks: str | list[str] | None = None,
) -> None:
    await memory.add(msgs, marks=marks)


async def compress_memory_after_turn(agent: ReActAgent) -> bool:
    """Summarize older messages once the exact recent window is exceeded.

    AgentScope's built-in compression is token-threshold based. For ordering
    conversations we also want a predictable shape after every turn:
    summary + recent exact messages. This helper reuses AgentScope's
    CompressionConfig and compression pipeline, but temporarily forces the
    threshold only when the message-count window has overflowed.
    """
    memory = getattr(agent, "memory", None)
    compression_config = getattr(agent, "compression_config", None)
    if (
        memory is None
        or compression_config is None
        or not compression_config.enable
        or not hasattr(memory, "get_uncompressed_messages")
        or not hasattr(agent, "_compress_memory_if_needed")
    ):
        return False

    uncompressed_msgs = await memory.get_uncompressed_messages()
    keep_recent = getattr(memory, "keep_recent", compression_config.keep_recent)
    if len(uncompressed_msgs) <= keep_recent:
        return False

    original_threshold = compression_config.trigger_threshold
    original_keep_recent = compression_config.keep_recent
    try:
        compression_config.trigger_threshold = 0
        compression_config.keep_recent = keep_recent
        await agent._compress_memory_if_needed()
    finally:
        compression_config.trigger_threshold = original_threshold
        compression_config.keep_recent = original_keep_recent

    return True


def make_token_counter(settings: Settings):
    provider = normalized_provider(settings)
    if provider in {"openai", "deepseek", "groq", "openrouter"}:
        return OpenAITokenCounter(settings.openai_model)
    return CharTokenCounter()


def make_chat_formatter(settings: Settings):
    kwargs = {
        "token_counter": make_token_counter(settings),
        "max_tokens": settings.memory_max_prompt_tokens,
    }
    provider = normalized_provider(settings)
    if provider == "anthropic":
        return AnthropicChatFormatter(**kwargs)
    if provider == "gemini":
        return GeminiChatFormatter(**kwargs)
    if provider == "ollama":
        return OllamaChatFormatter(**kwargs)
    if provider == "dashscope":
        return DashScopeChatFormatter(**kwargs)
    if provider == "deepseek":
        return DeepSeekChatFormatter(**kwargs)
    return OpenAIChatFormatter(**kwargs)


def make_multi_agent_formatter(settings: Settings):
    kwargs = {
        "token_counter": make_token_counter(settings),
        "max_tokens": settings.memory_max_prompt_tokens,
    }
    provider = normalized_provider(settings)
    if provider == "anthropic":
        return AnthropicMultiAgentFormatter(**kwargs)
    if provider == "gemini":
        return GeminiMultiAgentFormatter(**kwargs)
    if provider == "ollama":
        return OllamaMultiAgentFormatter(**kwargs)
    if provider == "dashscope":
        return DashScopeMultiAgentFormatter(**kwargs)
    if provider == "deepseek":
        return DeepSeekMultiAgentFormatter(**kwargs)
    return OpenAIMultiAgentFormatter(**kwargs)


def make_compression_config(settings: Settings) -> ReActAgent.CompressionConfig:
    return ReActAgent.CompressionConfig(
        enable=True,
        agent_token_counter=make_token_counter(settings),
        trigger_threshold=settings.memory_compression_trigger_tokens,
        keep_recent=_window_size(settings) + 1,
        compression_prompt=COMPRESSION_PROMPT,
        summary_template=SUMMARY_TEMPLATE,
        summary_schema=CafeConversationSummary,
    )
