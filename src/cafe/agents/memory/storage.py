"""Durable conversation memory storage for AgentScope chat turns.

This module owns SQL persistence, schema creation, per-request context
windowing, compact tool-result storage, and summary persistence.
"""

from __future__ import annotations

import asyncio
import json
import re
from copy import deepcopy
from hashlib import sha256
from inspect import currentframe
from pathlib import Path
from typing import Any

from agentscope.agent import ReActAgent
from agentscope.memory import MemoryBase
from agentscope.message import Msg, TextBlock
from pydantic import BaseModel, Field
from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    delete,
    func,
    insert,
    select,
    update,
)
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from cafe.core.validator import ValidationError
from cafe.models.cart import Cart
from cafe.models.menu import MenuItem
from cafe.models.order import Order
from cafe.services.menu_index_service import build_menu_item_match_index
from cafe.config import Settings, get_settings
from .summaries.models import create_memory_summaries_table
from .summaries.repositories import MemorySummaryRepository


DEFAULT_USER_ID = "anonymous"
SUMMARY_MARK = "summary"
TOOL_CALL_MARK = "tool_call"
TOOL_RESULT_MARK = "tool_result"
COMPRESSED_MARK = "compressed"
TOOL_RESULT_MAX_CHARS = 2000

_ENGINE: AsyncEngine | None = None
APP_MEMORY_METADATA = MetaData()

USERS_TABLE = Table(
    "users",
    APP_MEMORY_METADATA,
    Column("id", String(255), primary_key=True),
    Column("external_user_id", String(255), nullable=False),
    Column("name", String(255), nullable=True),
    Column("metadata", JSON, nullable=False, default=dict),
    Column("created_at", DateTime(timezone=True), server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), server_default=func.now()),
    UniqueConstraint("external_user_id", name="uq_users_external_user_id"),
)

CONVERSATIONS_TABLE = Table(
    "conversations",
    APP_MEMORY_METADATA,
    Column("id", String(255), primary_key=True),
    Column("user_id", String(255), ForeignKey("users.id"), nullable=False),
    Column("session_id", String(255), nullable=False),
    Column("title", String(255), nullable=True),
    Column("status", String(32), nullable=False, default="active"),
    Column("metadata", JSON, nullable=False, default=dict),
    Column("created_at", DateTime(timezone=True), server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), server_default=func.now()),
    UniqueConstraint("user_id", "session_id", name="uq_conversations_user_session"),
)

CONVERSATION_MESSAGES_TABLE = Table(
    "conversation_messages",
    APP_MEMORY_METADATA,
    Column("id", String(255), primary_key=True),
    Column("conversation_id", String(255), ForeignKey("conversations.id"), nullable=False),
    Column("sequence_no", Integer, nullable=False),
    Column("role", String(32), nullable=False),
    Column("name", String(255), nullable=False),
    Column("message_type", String(32), nullable=False),
    Column("content", JSON, nullable=False),
    Column("compact_content", Text, nullable=True),
    Column("tool_call_id", String(255), nullable=True),
    Column("tool_name", String(255), nullable=True),
    Column("marks", JSON, nullable=False, default=list),
    Column("visible_to_user", Boolean, nullable=False, default=False),
    Column("metadata", JSON, nullable=False, default=dict),
    Column("created_at", DateTime(timezone=True), server_default=func.now()),
    UniqueConstraint(
        "conversation_id",
        "sequence_no",
        name="uq_conversation_messages_sequence",
    ),
)

CONVERSATION_SUMMARIES_TABLE = Table(
    "conversation_summaries",
    APP_MEMORY_METADATA,
    Column("id", String(255), primary_key=True),
    Column("conversation_id", String(255), ForeignKey("conversations.id"), nullable=False),
    Column("summary", Text, nullable=False),
    Column("summary_version", Integer, nullable=False),
    Column("source_message_start", Integer, nullable=True),
    Column("source_message_end", Integer, nullable=True),
    Column("is_active", Boolean, nullable=False, default=True),
    Column("metadata", JSON, nullable=False, default=dict),
    Column("created_at", DateTime(timezone=True), server_default=func.now()),
)

MEMORY_SUMMARIES_TABLE = create_memory_summaries_table(APP_MEMORY_METADATA)

MENU_ITEMS_TABLE = Table(
    "menu_items",
    APP_MEMORY_METADATA,
    Column("id", String(255), primary_key=True),
    Column("name", String(255), nullable=False),
    Column("normalized_name", String(255), nullable=False),
    Column("top_level", String(255), nullable=False),
    Column("section", String(255), nullable=False),
    Column("price_inr", Integer, nullable=False),
    Column("serving", String(255), nullable=True),
    Column("dietary_tags", String(255), nullable=True),
    Column("tags", JSON, nullable=False, default=list),
    Column("description", Text, nullable=True),
    Column("available", Boolean, nullable=False, default=True),
    Column("metadata", JSON, nullable=False, default=dict),
    Column("created_at", DateTime(timezone=True), server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), server_default=func.now()),
    UniqueConstraint("normalized_name", name="uq_menu_items_normalized_name"),
)

CARTS_TABLE = Table(
    "carts",
    APP_MEMORY_METADATA,
    Column("id", String(255), primary_key=True),
    Column("conversation_id", String(255), ForeignKey("conversations.id"), nullable=False),
    Column("user_id", String(255), ForeignKey("users.id"), nullable=False),
    Column("session_id", String(255), nullable=False),
    Column("status", String(32), nullable=False, default="active"),
    Column("total_inr", Integer, nullable=False, default=0),
    Column("metadata", JSON, nullable=False, default=dict),
    Column("created_at", DateTime(timezone=True), server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), server_default=func.now()),
    UniqueConstraint("conversation_id", name="uq_carts_conversation"),
)

CART_ITEMS_TABLE = Table(
    "cart_items",
    APP_MEMORY_METADATA,
    Column("id", String(255), primary_key=True),
    Column("cart_id", String(255), ForeignKey("carts.id"), nullable=False),
    Column("item_id", String(255), nullable=False),
    Column("name", String(255), nullable=False),
    Column("unit_price_inr", Integer, nullable=False),
    Column("quantity", Integer, nullable=False),
    Column("customizations", JSON, nullable=False, default=list),
    Column("line_total_inr", Integer, nullable=False),
    Column("metadata", JSON, nullable=False, default=dict),
    Column("created_at", DateTime(timezone=True), server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), server_default=func.now()),
)

ORDERS_TABLE = Table(
    "orders",
    APP_MEMORY_METADATA,
    Column("id", String(255), primary_key=True),
    Column("conversation_id", String(255), ForeignKey("conversations.id"), nullable=False),
    Column("user_id", String(255), ForeignKey("users.id"), nullable=False),
    Column("session_id", String(255), nullable=False),
    Column("status", String(32), nullable=False),
    Column("total_inr", Integer, nullable=False),
    Column("metadata", JSON, nullable=False, default=dict),
    Column("created_at", DateTime(timezone=True), server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), server_default=func.now()),
)

ORDER_ITEMS_TABLE = Table(
    "order_items",
    APP_MEMORY_METADATA,
    Column("id", String(255), primary_key=True),
    Column("order_id", String(255), ForeignKey("orders.id"), nullable=False),
    Column("item_id", String(255), nullable=False),
    Column("name", String(255), nullable=False),
    Column("unit_price_inr", Integer, nullable=False),
    Column("quantity", Integer, nullable=False),
    Column("customizations", JSON, nullable=False, default=list),
    Column("line_total_inr", Integer, nullable=False),
    Column("metadata", JSON, nullable=False, default=dict),
    Column("created_at", DateTime(timezone=True), server_default=func.now()),
)


class CafeConversationSummary(BaseModel):
    """Structured fields the compression LLM must preserve."""

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
    return max(settings.memory_recent_messages, 1)


def _storage_session_id(user_id: str, session_id: str) -> str:
    return sha256(f"{user_id}\0{session_id}".encode("utf-8")).hexdigest()


def _cart_id(conversation_id: str) -> str:
    return sha256(f"{conversation_id}:cart".encode("utf-8")).hexdigest()


def _menu_item_id(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-")
    return f"menu-{slug}"


def _normalized_menu_name(name: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", name.casefold()).split())


def _first_price_inr(price_text: str | None) -> int | None:
    if not price_text:
        return None
    match = re.search(r"\d+", price_text)
    return int(match.group(0)) if match else None


def _line_id(parent_id: str, index: int, item_id: str, customizations: list[str]) -> str:
    key = json.dumps(
        [parent_id, index, item_id, customizations],
        ensure_ascii=False,
        sort_keys=True,
    )
    return sha256(key.encode("utf-8")).hexdigest()


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


def _normalize_async_database_url(database_url: str) -> tuple[str, dict[str, Any]]:
    """Adapt provider URLs, such as Neon, for SQLAlchemy async drivers."""
    url = make_url(database_url)
    if url.drivername != "postgresql+asyncpg":
        return database_url, {}

    query = dict(url.query)
    sslmode = query.pop("sslmode", None)
    if not sslmode:
        return database_url, {}

    normalized_url = url.set(query=query)
    if str(sslmode).lower() in {"require", "verify-ca", "verify-full"}:
        return normalized_url.render_as_string(hide_password=False), {
            "connect_args": {"ssl": True, "timeout": 15},
        }

    return normalized_url.render_as_string(hide_password=False), {}


def _get_engine(settings: Settings) -> AsyncEngine:
    global _ENGINE
    if _ENGINE is None:
        _ensure_sqlite_parent(settings.memory_database_url)
        url, connect_args = _normalize_async_database_url(
            settings.memory_database_url,
        )
        _ENGINE = create_async_engine(url, pool_pre_ping=True, **connect_args)
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


def _tool_name(block: Any) -> str | None:
    if isinstance(block, dict):
        return block.get("name")
    return getattr(block, "name", None)


def _message_type(msg: Msg) -> str:
    if msg.metadata.get("kind") == SUMMARY_MARK:
        return SUMMARY_MARK
    if msg.has_content_blocks("tool_use"):
        return TOOL_CALL_MARK
    if msg.has_content_blocks("tool_result"):
        return TOOL_RESULT_MARK
    return msg.role


def _compact_content(msg: Msg) -> str:
    display_text = msg.metadata.get("display_text") or msg.metadata.get("user_text")
    if display_text:
        return _truncate(str(display_text))
    return _truncate(msg.get_text_content() or "")


class AppSQLMemory(MemoryBase):
    """AgentScope-compatible memory backed by the app conversation schema."""

    def __init__(
        self,
        engine: AsyncEngine,
        *,
        user_id: str,
        session_id: str,
        keep_recent: int,
    ) -> None:
        super().__init__()
        self.engine = engine
        self.user_id = user_id
        self.session_id = session_id
        self.conversation_id = _storage_session_id(user_id, session_id)
        self.keep_recent = keep_recent
        self._summary_hydrated = False
        self._initialized = False
        self._lock = asyncio.Lock()

    def _summary_repo(self) -> MemorySummaryRepository:
        return MemorySummaryRepository(
            self.engine,
            summary_table=MEMORY_SUMMARIES_TABLE,
            message_table=CONVERSATION_MESSAGES_TABLE,
        )

    async def _create_table(self) -> None:
        if self._initialized:
            return

        async with self.engine.begin() as conn:
            await conn.run_sync(APP_MEMORY_METADATA.create_all)
            user_exists = await conn.scalar(
                select(USERS_TABLE.c.id).where(USERS_TABLE.c.id == self.user_id)
            )
            if not user_exists:
                await conn.execute(
                    insert(USERS_TABLE).values(
                        id=self.user_id,
                        external_user_id=self.user_id,
                        metadata={},
                    )
                )

            conversation_exists = await conn.scalar(
                select(CONVERSATIONS_TABLE.c.id).where(
                    CONVERSATIONS_TABLE.c.id == self.conversation_id
                )
            )
            if not conversation_exists:
                await conn.execute(
                    insert(CONVERSATIONS_TABLE).values(
                        id=self.conversation_id,
                        user_id=self.user_id,
                        session_id=self.session_id,
                        status="active",
                        metadata={},
                    )
                )

        self._initialized = True

    async def _hydrate_summary(self) -> None:
        await self._create_table()
        self._compressed_summary = await self._summary_repo().latest_summary_text(
            self.conversation_id
        )
        self._summary_hydrated = True

    async def _next_sequence(self, conn) -> int:
        max_sequence = await conn.scalar(
            select(func.max(CONVERSATION_MESSAGES_TABLE.c.sequence_no)).where(
                CONVERSATION_MESSAGES_TABLE.c.conversation_id == self.conversation_id
            )
        )
        return (max_sequence or 0) + 1

    async def _fetch_message_rows(self) -> list[dict[str, Any]]:
        await self._create_table()
        async with self.engine.connect() as conn:
            rows = (
                await conn.execute(
                    select(CONVERSATION_MESSAGES_TABLE)
                    .where(
                        CONVERSATION_MESSAGES_TABLE.c.conversation_id
                        == self.conversation_id
                    )
                    .order_by(CONVERSATION_MESSAGES_TABLE.c.sequence_no)
                )
            ).mappings().all()
        return [dict(row) for row in rows]

    def _msg_from_row(self, row: dict[str, Any]) -> Msg:
        msg = Msg.from_dict(row["content"])
        msg.id = row["id"]
        return msg

    def _active_summary_msg(self) -> Msg | None:
        if not self._compressed_summary:
            return None
        return Msg(
            "memory",
            self._compressed_summary,
            "user",
            metadata={"kind": SUMMARY_MARK},
        )

    def _filter_rows(
        self,
        rows: list[dict[str, Any]],
        mark: str | None,
        exclude_mark: str | None,
    ) -> list[dict[str, Any]]:
        filtered = []
        for row in rows:
            marks = set(row.get("marks") or [])
            if mark is not None and mark not in marks:
                continue
            if exclude_mark is not None and exclude_mark in marks:
                continue
            filtered.append(row)
        return filtered

    async def update_compressed_summary(self, summary: str) -> None:
        # AgentScope keeps the active summary on the memory object; this also
        # persists it for restarts and UI history.
        await self._create_table()
        await super().update_compressed_summary(summary)
        async with self._lock:
            async with self.engine.begin() as conn:
                await conn.execute(
                    update(CONVERSATION_SUMMARIES_TABLE)
                    .where(
                        CONVERSATION_SUMMARIES_TABLE.c.conversation_id
                        == self.conversation_id
                    )
                    .values(is_active=False)
                )
                next_version = (
                    await conn.scalar(
                        select(
                            func.max(
                                CONVERSATION_SUMMARIES_TABLE.c.summary_version
                            )
                        ).where(
                            CONVERSATION_SUMMARIES_TABLE.c.conversation_id
                            == self.conversation_id
                        )
                    )
                    or 0
                ) + 1
                await conn.execute(
                    insert(CONVERSATION_SUMMARIES_TABLE).values(
                        id=sha256(
                            f"{self.conversation_id}:{next_version}".encode("utf-8")
                        ).hexdigest(),
                        conversation_id=self.conversation_id,
                        summary=summary,
                        summary_version=next_version,
                        source_message_start=None,
                        source_message_end=None,
                        is_active=True,
                        metadata={"kind": SUMMARY_MARK},
                    )
                )
        self._summary_hydrated = True

    async def clear(self) -> None:
        async with self._lock:
            await self._create_table()
            await self._summary_repo().delete_for_conversation(self.conversation_id)
            async with self.engine.begin() as conn:
                await conn.execute(
                    delete(CONVERSATION_SUMMARIES_TABLE).where(
                        CONVERSATION_SUMMARIES_TABLE.c.conversation_id
                        == self.conversation_id
                    )
                )
                await conn.execute(
                    delete(CONVERSATION_MESSAGES_TABLE).where(
                        CONVERSATION_MESSAGES_TABLE.c.conversation_id
                        == self.conversation_id
                    )
                )
        self._compressed_summary = ""
        self._summary_hydrated = True

    async def delete_by_mark(self, mark: str | list[str], **kwargs: Any) -> int:
        await self._create_table()
        marks = [mark] if isinstance(mark, str) else mark
        if SUMMARY_MARK in marks:
            await self._summary_repo().delete_for_conversation(self.conversation_id)
            async with self.engine.begin() as conn:
                await conn.execute(
                    update(CONVERSATION_SUMMARIES_TABLE)
                    .where(
                        CONVERSATION_SUMMARIES_TABLE.c.conversation_id
                        == self.conversation_id
                    )
                    .values(is_active=False)
                )
            self._compressed_summary = ""
            self._summary_hydrated = True

        rows = await self._fetch_message_rows()
        msg_ids = [
            row["id"]
            for row in rows
            if set(row.get("marks") or []).intersection(marks)
        ]
        return await self.delete(msg_ids)

    async def get_uncompressed_messages(self) -> list[Msg]:
        """Return full uncompressed DB messages, excluding the summary marker."""
        rows = self._filter_rows(
            await self._fetch_message_rows(),
            mark=None,
            exclude_mark=COMPRESSED_MARK,
        )
        return [self._msg_from_row(row) for row in rows]

    async def get_memory(
        self,
        mark: str | None = None,
        exclude_mark: str | None = None,
        prepend_summary: bool = True,
        **kwargs: Any,
    ) -> list[Msg]:
        await self._hydrate_summary()

        if mark == SUMMARY_MARK:
            summary = self._active_summary_msg()
            return [summary] if summary else []

        rows = self._filter_rows(await self._fetch_message_rows(), mark, exclude_mark)

        if exclude_mark == COMPRESSED_MARK and not _called_from_compression():
            rows = [row for row in rows if row.get("visible_to_user")]

        msgs = [self._msg_from_row(row) for row in rows]

        # Compression receives full uncompressed history. Normal prompt
        # construction receives summary + recent visible messages only.
        if exclude_mark == COMPRESSED_MARK and not _called_from_compression():
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

        if prepend_summary and self._compressed_summary:
            return [Msg("memory", self._compressed_summary, "user"), *msgs]
        return msgs

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
        skip_duplicated = kwargs.get("skip_duplicated", True)
        async with self._lock:
            await self._create_table()
            async with self.engine.begin() as conn:
                sequence_no = await self._next_sequence(conn)
                inserted_visible = False
                title_candidate = None
                for original in memories:
                    for msg in _split_mixed_tool_messages(original):
                        msg = _compact_tool_result_msg(msg)
                        if skip_duplicated and await conn.scalar(
                            select(CONVERSATION_MESSAGES_TABLE.c.id).where(
                                CONVERSATION_MESSAGES_TABLE.c.id == msg.id
                            )
                        ):
                            continue

                        msg_marks = list(base_marks)
                        if msg.has_content_blocks("tool_use"):
                            msg_marks.append(TOOL_CALL_MARK)
                        if msg.has_content_blocks("tool_result"):
                            msg_marks.append(TOOL_RESULT_MARK)

                        tool_use = next(iter(msg.get_content_blocks("tool_use")), None)
                        tool_result = next(
                            iter(msg.get_content_blocks("tool_result")),
                            None,
                        )
                        tool_block = tool_use or tool_result
                        visible = (
                            msg.role in {"user", "assistant"}
                            and not msg.has_content_blocks("tool_use")
                            and not msg.has_content_blocks("tool_result")
                            and msg.metadata.get("kind") != SUMMARY_MARK
                        )
                        compact = _compact_content(msg)
                        if visible:
                            inserted_visible = True
                            if msg.role == "user" and not title_candidate:
                                title_candidate = compact
                        await conn.execute(
                            insert(CONVERSATION_MESSAGES_TABLE).values(
                                id=msg.id,
                                conversation_id=self.conversation_id,
                                sequence_no=sequence_no,
                                role=msg.role,
                                name=msg.name,
                                message_type=_message_type(msg),
                                content=msg.to_dict(),
                                compact_content=compact,
                                tool_call_id=_block_id(tool_block)
                                if tool_block
                                else None,
                                tool_name=_tool_name(tool_block)
                                if tool_block
                                else None,
                                marks=sorted(set(msg_marks)),
                                visible_to_user=visible,
                                metadata=msg.metadata,
                            )
                        )
                        sequence_no += 1
                if inserted_visible:
                    current_title = await conn.scalar(
                        select(CONVERSATIONS_TABLE.c.title).where(
                            CONVERSATIONS_TABLE.c.id == self.conversation_id
                        )
                    )
                    values = {"updated_at": func.now()}
                    if not current_title and title_candidate:
                        values["title"] = _truncate(title_candidate, 80)
                    await conn.execute(
                        update(CONVERSATIONS_TABLE)
                        .where(CONVERSATIONS_TABLE.c.id == self.conversation_id)
                        .values(**values)
                    )

    async def delete(self, msg_ids: list[str], **kwargs: Any) -> int:
        if not msg_ids:
            return 0
        await self._create_table()
        async with self._lock:
            async with self.engine.begin() as conn:
                result = await conn.execute(
                    delete(CONVERSATION_MESSAGES_TABLE).where(
                        CONVERSATION_MESSAGES_TABLE.c.conversation_id
                        == self.conversation_id,
                        CONVERSATION_MESSAGES_TABLE.c.id.in_(msg_ids),
                    )
                )
        return result.rowcount or 0

    async def size(self) -> int:
        await self._create_table()
        async with self.engine.connect() as conn:
            return int(
                await conn.scalar(
                    select(func.count(CONVERSATION_MESSAGES_TABLE.c.id)).where(
                        CONVERSATION_MESSAGES_TABLE.c.conversation_id
                        == self.conversation_id
                    )
                )
                or 0
            )

    async def update_messages_mark(
        self,
        new_mark: str | None,
        old_mark: str | None = None,
        msg_ids: list[str] | None = None,
    ) -> int:
        await self._create_table()
        rows = await self._fetch_message_rows()
        updated = 0
        async with self._lock:
            async with self.engine.begin() as conn:
                for row in rows:
                    if msg_ids is not None and row["id"] not in msg_ids:
                        continue
                    marks = list(row.get("marks") or [])
                    if old_mark is not None and old_mark not in marks:
                        continue

                    changed = False
                    if new_mark is None:
                        if old_mark in marks:
                            marks.remove(old_mark)
                            changed = True
                    else:
                        if old_mark is not None and old_mark in marks:
                            marks.remove(old_mark)
                            changed = True
                        if new_mark not in marks:
                            marks.append(new_mark)
                            changed = True

                    if changed:
                        await conn.execute(
                            update(CONVERSATION_MESSAGES_TABLE)
                            .where(CONVERSATION_MESSAGES_TABLE.c.id == row["id"])
                            .values(marks=sorted(set(marks)))
                        )
                        updated += 1
        return updated

    async def close(self) -> None:
        return None


def load_memory(
    session_id: str,
    user_id: str = DEFAULT_USER_ID,
    settings: Settings | None = None,
) -> AppSQLMemory:
    """Create SQL-backed memory keyed by user_id and session_id."""
    settings = settings or get_settings()
    return AppSQLMemory(
        _get_engine(settings),
        user_id=user_id,
        session_id=session_id,
        keep_recent=_window_size(settings),
    )


async def list_user_conversations(
    user_id: str = DEFAULT_USER_ID,
    *,
    limit: int = 20,
    settings: Settings | None = None,
) -> list[dict[str, Any]]:
    """Return recent SQL conversations for the frontend sidebar."""
    settings = settings or get_settings()
    engine = _get_engine(settings)
    await ensure_menu_catalog(settings)

    last_visible = (
        select(
            CONVERSATION_MESSAGES_TABLE.c.conversation_id,
            func.max(CONVERSATION_MESSAGES_TABLE.c.sequence_no).label("last_sequence"),
            func.count(CONVERSATION_MESSAGES_TABLE.c.id).label("message_count"),
        )
        .where(CONVERSATION_MESSAGES_TABLE.c.visible_to_user.is_(True))
        .group_by(CONVERSATION_MESSAGES_TABLE.c.conversation_id)
        .subquery()
    )

    query = (
        select(
            CONVERSATIONS_TABLE.c.session_id,
            CONVERSATIONS_TABLE.c.title,
            CONVERSATIONS_TABLE.c.status,
            CONVERSATIONS_TABLE.c.created_at,
            CONVERSATIONS_TABLE.c.updated_at,
            last_visible.c.message_count,
            CONVERSATION_MESSAGES_TABLE.c.compact_content.label("last_message"),
        )
        .select_from(
            CONVERSATIONS_TABLE.outerjoin(
                last_visible,
                CONVERSATIONS_TABLE.c.id == last_visible.c.conversation_id,
            ).outerjoin(
                CONVERSATION_MESSAGES_TABLE,
                (
                    CONVERSATION_MESSAGES_TABLE.c.conversation_id
                    == last_visible.c.conversation_id
                )
                & (
                    CONVERSATION_MESSAGES_TABLE.c.sequence_no
                    == last_visible.c.last_sequence
                ),
            )
        )
        .where(CONVERSATIONS_TABLE.c.user_id == user_id)
        .order_by(CONVERSATIONS_TABLE.c.updated_at.desc())
        .limit(limit)
    )

    async with engine.connect() as conn:
        rows = (await conn.execute(query)).mappings().all()

    conversations = []
    for row in rows:
        last_message = row["last_message"] or ""
        conversations.append(
            {
                "session_id": row["session_id"],
                "title": row["title"] or last_message or "New chat",
                "status": row["status"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "last_message": last_message,
                "message_count": int(row["message_count"] or 0),
            }
        )
    return conversations


async def list_conversation_messages(
    session_id: str,
    user_id: str = DEFAULT_USER_ID,
    *,
    limit: int = 200,
    settings: Settings | None = None,
) -> list[dict[str, Any]]:
    """Return visible user/assistant messages for one conversation."""
    settings = settings or get_settings()
    engine = _get_engine(settings)
    conversation_id = _storage_session_id(user_id, session_id)
    async with engine.begin() as conn:
        await conn.run_sync(APP_MEMORY_METADATA.create_all)
        rows = (
            await conn.execute(
                select(
                    CONVERSATION_MESSAGES_TABLE.c.id,
                    CONVERSATION_MESSAGES_TABLE.c.sequence_no,
                    CONVERSATION_MESSAGES_TABLE.c.role,
                    CONVERSATION_MESSAGES_TABLE.c.name,
                    CONVERSATION_MESSAGES_TABLE.c.compact_content,
                    CONVERSATION_MESSAGES_TABLE.c.created_at,
                )
                .where(
                    CONVERSATION_MESSAGES_TABLE.c.conversation_id == conversation_id,
                    CONVERSATION_MESSAGES_TABLE.c.visible_to_user.is_(True),
                )
                .order_by(CONVERSATION_MESSAGES_TABLE.c.sequence_no)
                .limit(limit)
            )
        ).mappings().all()

    return [
        {
            "id": row["id"],
            "sequence_no": row["sequence_no"],
            "role": row["role"],
            "name": row["name"],
            "content": row["compact_content"] or "",
            "created_at": row["created_at"],
        }
        for row in rows
    ]


async def ensure_menu_catalog(settings: Settings | None = None) -> None:
    """Seed SQL menu_items from the canonical parsed menu document."""
    settings = settings or get_settings()
    engine = _get_engine(settings)
    async with engine.begin() as conn:
        await conn.run_sync(APP_MEMORY_METADATA.create_all)
        existing_count = await conn.scalar(select(func.count(MENU_ITEMS_TABLE.c.id)))
        if existing_count:
            return

        for item in build_menu_item_match_index():
            price = _first_price_inr(item.price)
            if price is None:
                continue
            await conn.execute(
                insert(MENU_ITEMS_TABLE).values(
                    id=_menu_item_id(item.name),
                    name=item.name,
                    normalized_name=_normalized_menu_name(item.name),
                    top_level=item.top_level,
                    section=item.section,
                    price_inr=price,
                    serving=item.serving,
                    dietary_tags=item.dietary_tags,
                    tags=list(item.tags),
                    description=item.description,
                    available=True,
                    metadata={"source": "BTB_Menu_Enhanced.md"},
                )
            )


async def resolve_menu_item_for_cart(
    item_ref: str,
    settings: Settings | None = None,
) -> MenuItem:
    """Resolve an exact SQL menu item id or exact item name for cart tools."""
    settings = settings or get_settings()
    await ensure_menu_catalog(settings)
    engine = _get_engine(settings)
    normalized = _normalized_menu_name(item_ref)

    async with engine.connect() as conn:
        row = (
            await conn.execute(
                select(MENU_ITEMS_TABLE).where(
                    (MENU_ITEMS_TABLE.c.id == item_ref)
                    | (MENU_ITEMS_TABLE.c.normalized_name == normalized)
                )
            )
        ).mappings().first()

    if row is None:
        raise ValidationError(f"Unknown menu item: {item_ref}")

    tags = list(row["tags"] or [])
    for tag in (row["top_level"], row["section"], row["serving"]):
        if tag and tag not in tags:
            tags.append(tag)

    return MenuItem(
        id=row["id"],
        name=row["name"],
        category=row["section"],
        price_inr=row["price_inr"],
        available=row["available"],
        tags=tags,
    )


async def save_cart_snapshot(
    session_id: str,
    cart: Cart,
    user_id: str = DEFAULT_USER_ID,
    settings: Settings | None = None,
) -> None:
    """Persist the latest current-cart snapshot for a conversation."""
    memory = load_memory(session_id, user_id=user_id, settings=settings)
    await memory._create_table()

    cart_id = _cart_id(memory.conversation_id)
    async with memory._lock:
        async with memory.engine.begin() as conn:
            exists = await conn.scalar(
                select(CARTS_TABLE.c.id).where(CARTS_TABLE.c.id == cart_id)
            )
            values = {
                "conversation_id": memory.conversation_id,
                "user_id": user_id,
                "session_id": session_id,
                "status": "active",
                "total_inr": cart.total_inr,
                "metadata": {},
            }
            if exists:
                await conn.execute(
                    update(CARTS_TABLE)
                    .where(CARTS_TABLE.c.id == cart_id)
                    .values(**values, updated_at=func.now())
                )
            else:
                await conn.execute(insert(CARTS_TABLE).values(id=cart_id, **values))

            await conn.execute(
                delete(CART_ITEMS_TABLE).where(CART_ITEMS_TABLE.c.cart_id == cart_id)
            )
            for index, item in enumerate(cart.items):
                await conn.execute(
                    insert(CART_ITEMS_TABLE).values(
                        id=_line_id(
                            cart_id,
                            index,
                            item.item_id,
                            item.customizations,
                        ),
                        cart_id=cart_id,
                        item_id=item.item_id,
                        name=item.name,
                        unit_price_inr=item.unit_price_inr,
                        quantity=item.quantity,
                        customizations=item.customizations,
                        line_total_inr=item.line_total_inr,
                        metadata={},
                    )
                )


async def clear_cart_snapshot(
    session_id: str,
    user_id: str = DEFAULT_USER_ID,
    settings: Settings | None = None,
) -> None:
    """Persist an empty current cart after checkout or manual clear."""
    await save_cart_snapshot(
        session_id,
        Cart(session_id=session_id),
        user_id=user_id,
        settings=settings,
    )


async def delete_session_data(
    session_id: str,
    user_id: str = DEFAULT_USER_ID,
    settings: Settings | None = None,
) -> None:
    """Delete persisted memory, cart, and order data for one user/session."""
    settings = settings or get_settings()
    engine = _get_engine(settings)
    conversation_id = _storage_session_id(user_id, session_id)
    summary_repo = MemorySummaryRepository(
        engine,
        summary_table=MEMORY_SUMMARIES_TABLE,
        message_table=CONVERSATION_MESSAGES_TABLE,
    )

    async with engine.begin() as conn:
        await conn.run_sync(APP_MEMORY_METADATA.create_all)

        cart_ids = [
            row.id
            for row in (
                await conn.execute(
                    select(CARTS_TABLE.c.id).where(
                        CARTS_TABLE.c.conversation_id == conversation_id
                    )
                )
            )
        ]
        order_ids = [
            row.id
            for row in (
                await conn.execute(
                    select(ORDERS_TABLE.c.id).where(
                        ORDERS_TABLE.c.conversation_id == conversation_id
                    )
                )
            )
        ]

        if order_ids:
            await conn.execute(
                delete(ORDER_ITEMS_TABLE).where(
                    ORDER_ITEMS_TABLE.c.order_id.in_(order_ids)
                )
            )
        await conn.execute(
            delete(ORDERS_TABLE).where(
                ORDERS_TABLE.c.conversation_id == conversation_id
            )
        )

        if cart_ids:
            await conn.execute(
                delete(CART_ITEMS_TABLE).where(CART_ITEMS_TABLE.c.cart_id.in_(cart_ids))
            )
        await conn.execute(
            delete(CARTS_TABLE).where(CARTS_TABLE.c.conversation_id == conversation_id)
        )

        await summary_repo.delete_for_conversation(conversation_id, conn=conn)
        await conn.execute(
            delete(CONVERSATION_SUMMARIES_TABLE).where(
                CONVERSATION_SUMMARIES_TABLE.c.conversation_id == conversation_id
            )
        )
        await conn.execute(
            delete(CONVERSATION_MESSAGES_TABLE).where(
                CONVERSATION_MESSAGES_TABLE.c.conversation_id == conversation_id
            )
        )
        await conn.execute(
            delete(CONVERSATIONS_TABLE).where(CONVERSATIONS_TABLE.c.id == conversation_id)
        )


async def save_order_snapshot(
    order: Order,
    user_id: str = DEFAULT_USER_ID,
    settings: Settings | None = None,
) -> None:
    """Persist or update an order and its immutable line-item snapshot."""
    memory = load_memory(order.session_id, user_id=user_id, settings=settings)
    await memory._create_table()

    async with memory._lock:
        async with memory.engine.begin() as conn:
            exists = await conn.scalar(
                select(ORDERS_TABLE.c.id).where(ORDERS_TABLE.c.id == order.order_id)
            )
            values = {
                "conversation_id": memory.conversation_id,
                "user_id": user_id,
                "session_id": order.session_id,
                "status": order.status,
                "total_inr": order.total_inr,
                "metadata": {"model_created_at": order.created_at.isoformat()},
            }
            if exists:
                await conn.execute(
                    update(ORDERS_TABLE)
                    .where(ORDERS_TABLE.c.id == order.order_id)
                    .values(**values, updated_at=func.now())
                )
            else:
                await conn.execute(
                    insert(ORDERS_TABLE).values(id=order.order_id, **values)
                )

            await conn.execute(
                delete(ORDER_ITEMS_TABLE).where(
                    ORDER_ITEMS_TABLE.c.order_id == order.order_id
                )
            )
            for index, item in enumerate(order.items):
                await conn.execute(
                    insert(ORDER_ITEMS_TABLE).values(
                        id=_line_id(
                            order.order_id,
                            index,
                            item.item_id,
                            item.customizations,
                        ),
                        order_id=order.order_id,
                        item_id=item.item_id,
                        name=item.name,
                        unit_price_inr=item.unit_price_inr,
                        quantity=item.quantity,
                        customizations=item.customizations,
                        line_total_inr=item.line_total_inr,
                        metadata={},
                    )
                )


async def get_summary(memory: MemoryBase) -> Msg | None:
    summaries = await memory.get_memory(mark=SUMMARY_MARK, prepend_summary=False)
    return summaries[-1] if summaries else None


async def get_recent_messages(memory: MemoryBase) -> list[Msg]:
    return await memory.get_memory(
        exclude_mark=COMPRESSED_MARK,
        prepend_summary=False,
    )


async def build_context(memory: MemoryBase) -> list[Msg]:
    """Return prompt messages in the required order before the current input."""
    from cafe.agents.prompts import ORCHESTRATOR_PROMPT

    msgs = [Msg("system", ORCHESTRATOR_PROMPT, "system")]
    if summary := await get_summary(memory):
        msgs.append(summary)
    msgs.extend(await get_recent_messages(memory))
    return msgs


async def save_messages(
    memory: MemoryBase,
    msgs: Msg | list[Msg],
    marks: str | list[str] | None = None,
) -> None:
    await memory.add(msgs, marks=marks)


async def compress_memory_after_turn(agent: ReActAgent) -> bool:
    """Summarize older messages once the exact recent window is exceeded."""
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
