"""Durable conversation memory storage for AgentScope chat turns.

This module owns SQL persistence, schema creation, per-request context
windowing, compact tool-result storage, and summary persistence.
"""

from __future__ import annotations

import asyncio
import json
import re
from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
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
    Index,
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
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from cafe.config import Settings, get_settings
from cafe.core.observability import observed_span
from cafe.core.validator import ValidationError
from cafe.models.cart import Cart
from cafe.models.menu import MenuItem
from cafe.models.order import Order
from cafe.services.menu_index_service import build_menu_item_match_index

from .summary_cache import (
    clear_summary_cache_sync,
    delete_cached_summary,
    get_cached_summary,
    set_cached_summary,
)

DEFAULT_USER_ID = "anonymous"
SUMMARY_MARK = "summary"
TOOL_CALL_MARK = "tool_call"
TOOL_RESULT_MARK = "tool_result"
COMPRESSED_MARK = "compressed"
TOOL_RESULT_MAX_CHARS = 2000

_ENGINE: AsyncEngine | None = None
_SCHEMA_INITIALIZED: set[str] = set()
_MENU_CATALOG_INITIALIZED = False
_ENSURED_CONVERSATIONS: set[tuple[str, str]] = set()
_STORAGE_INIT_LOCK = asyncio.Lock()
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
    Column("last_sequence_no", Integer, nullable=False, default=0),
    Column("last_compressed_sequence_no", Integer, nullable=False, default=0),
    Column("metadata", JSON, nullable=False, default=dict),
    Column("created_at", DateTime(timezone=True), server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), server_default=func.now()),
    UniqueConstraint("user_id", "session_id", name="uq_conversations_user_session"),
)

CONVERSATION_MESSAGES_TABLE = Table(
    "conversation_messages",
    APP_MEMORY_METADATA,
    Column("id", String(255), primary_key=True),
    Column(
        "conversation_id", String(255), ForeignKey("conversations.id"), nullable=False
    ),
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
    Column("is_compressed", Boolean, nullable=False, default=False),
    Column("metadata", JSON, nullable=False, default=dict),
    Column("created_at", DateTime(timezone=True), server_default=func.now()),
    UniqueConstraint(
        "conversation_id",
        "sequence_no",
        name="uq_conversation_messages_sequence",
    ),
)

Index(
    "ix_conversation_messages_visible_sequence",
    CONVERSATION_MESSAGES_TABLE.c.conversation_id,
    CONVERSATION_MESSAGES_TABLE.c.visible_to_user,
    CONVERSATION_MESSAGES_TABLE.c.sequence_no,
)
Index(
    "ix_conversation_messages_compressed_sequence",
    CONVERSATION_MESSAGES_TABLE.c.conversation_id,
    CONVERSATION_MESSAGES_TABLE.c.is_compressed,
    CONVERSATION_MESSAGES_TABLE.c.sequence_no,
)

CONVERSATION_SUMMARIES_TABLE = Table(
    "conversation_summaries",
    APP_MEMORY_METADATA,
    Column("id", String(255), primary_key=True),
    Column(
        "conversation_id", String(255), ForeignKey("conversations.id"), nullable=False
    ),
    Column("summary", Text, nullable=False),
    Column("summary_version", Integer, nullable=False),
    Column("source_message_start", Integer, nullable=True),
    Column("source_message_end", Integer, nullable=True),
    Column("is_active", Boolean, nullable=False, default=True),
    Column("metadata", JSON, nullable=False, default=dict),
    Column("created_at", DateTime(timezone=True), server_default=func.now()),
)

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
    Column(
        "conversation_id", String(255), ForeignKey("conversations.id"), nullable=False
    ),
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
    Column(
        "conversation_id", String(255), ForeignKey("conversations.id"), nullable=False
    ),
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


@dataclass(frozen=True)
class SummaryCheckpoint:
    """A fixed-size memory window that should become the next summary."""

    previous_summary: str
    messages: tuple[Msg, ...]
    message_ids: tuple[str, ...]
    source_message_start: int
    source_message_end: int


SUMMARY_TEMPLATE = (
    "<conversation_summary>"
    "Key user decisions: {key_user_decisions}\n"
    "Important facts: {important_facts}\n"
    "Unresolved questions: {unresolved_questions}"
    "</conversation_summary>"
)

COMPRESSION_PROMPT = (
    "Create the next cumulative short-term memory summary for Milo, the cafe "
    "assistant. Use the previous summary plus only the new checkpoint messages. "
    "Preserve key user decisions, preferences, dietary or health constraints, "
    "important cart/order/menu facts, unresolved questions, and promises made. "
    "Do not invent facts. Do not include messages outside this checkpoint."
)


def _window_size(settings: Settings) -> int:
    """Handle window size.

    Args:
        - settings: Settings - The settings value.

    Returns:
        - return int - The return value.
    """
    return min(max(settings.memory_keep_recent_messages, 8), 12)


def _storage_session_id(user_id: str, session_id: str) -> str:
    """Handle storage session id.

    Args:
        - user_id: str - The user id value.
        - session_id: str - The session id value.

    Returns:
        - return str - The return value.
    """
    return sha256(f"{user_id}\0{session_id}".encode("utf-8")).hexdigest()


def _cart_id(conversation_id: str) -> str:
    """Handle cart id.

    Args:
        - conversation_id: str - The conversation id value.

    Returns:
        - return str - The return value.
    """
    return sha256(f"{conversation_id}:cart".encode("utf-8")).hexdigest()


def _menu_item_id(name: str) -> str:
    """Handle menu item id.

    Args:
        - name: str - The name value.

    Returns:
        - return str - The return value.
    """
    slug = re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-")
    return f"menu-{slug}"


def _normalized_menu_name(name: str) -> str:
    """Handle normalized menu name.

    Args:
        - name: str - The name value.

    Returns:
        - return str - The return value.
    """
    return " ".join(re.sub(r"[^a-z0-9]+", " ", name.casefold()).split())


def _first_price_inr(price_text: str | None) -> int | None:
    """Handle first price inr.

    Args:
        - price_text: str | None - The price text value.

    Returns:
        - return int | None - The return value.
    """
    if not price_text:
        return None
    match = re.search(r"\d+", price_text)
    return int(match.group(0)) if match else None


def _line_id(
    parent_id: str, index: int, item_id: str, customizations: list[str]
) -> str:
    """Handle line id.

    Args:
        - parent_id: str - The parent id value.
        - index: int - The index value.
        - item_id: str - The item id value.
        - customizations: list[str] - The customizations value.

    Returns:
        - return str - The return value.
    """
    key = json.dumps(
        [parent_id, index, item_id, customizations],
        ensure_ascii=False,
        sort_keys=True,
    )
    return sha256(key.encode("utf-8")).hexdigest()


def _ensure_sqlite_parent(database_url: str) -> None:
    """Handle ensure sqlite parent.

    Args:
        - database_url: str - The database url value.

    Returns:
        - return None - The return value.
    """
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
    """Adapt provider URLs, such as Neon, for SQLAlchemy async drivers.

    Args:
        - database_url: str - The database url value.

    Returns:
        - return tuple[str, dict[str, Any]] - The return value.
    """
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
    """Handle get engine.

    Args:
        - settings: Settings - The settings value.

    Returns:
        - return AsyncEngine - The return value.
    """
    global _ENGINE
    if _ENGINE is None:
        _ensure_sqlite_parent(settings.memory_database_url)
        url, connect_args = _normalize_async_database_url(
            settings.memory_database_url,
        )
        engine_kwargs: dict[str, Any] = dict(connect_args)
        if not url.startswith("sqlite+aiosqlite"):
            engine_kwargs.update(
                pool_size=settings.memory_db_pool_size,
                max_overflow=settings.memory_db_max_overflow,
                pool_timeout=settings.memory_db_pool_timeout,
                pool_recycle=settings.memory_db_pool_recycle,
                pool_pre_ping=settings.memory_db_pool_pre_ping,
            )
        _ENGINE = create_async_engine(url, **engine_kwargs)
    return _ENGINE


def _reset_storage_runtime_cache() -> None:
    """Reset module caches when tests swap databases.

    Returns:
        - return None - The return value.
    """
    global _ENGINE, _MENU_CATALOG_INITIALIZED
    _ENGINE = None
    _SCHEMA_INITIALIZED.clear()
    _MENU_CATALOG_INITIALIZED = False
    _ENSURED_CONVERSATIONS.clear()
    clear_summary_cache_sync()


def _insert_ignore(table: Table, values: dict[str, Any], dialect_name: str):
    """Build a cross-dialect insert that ignores duplicate-key conflicts.

    Args:
        - table: Table - The table value.
        - values: dict[str, Any] - The values value.
        - dialect_name: str - The dialect name value.

    Returns:
        - return Any - The return value.
    """
    if dialect_name == "postgresql":
        return postgresql_insert(table).values(**values).on_conflict_do_nothing()
    if dialect_name == "sqlite":
        return sqlite_insert(table).values(**values).on_conflict_do_nothing()
    return insert(table).values(**values)


def _insert_many_ignore(table: Table, rows: list[dict[str, Any]], dialect_name: str):
    """Build a cross-dialect batch insert that ignores duplicate conflicts.

    Args:
        - table: Table - The table value.
        - rows: list[dict[str, Any]] - The row values.
        - dialect_name: str - The SQL dialect name.

    Returns:
        - return Any - The insert statement.
    """
    if dialect_name == "postgresql":
        return postgresql_insert(table).values(rows).on_conflict_do_nothing()
    if dialect_name == "sqlite":
        return sqlite_insert(table).values(rows).on_conflict_do_nothing()
    return insert(table).values(rows)


async def _ensure_schema(engine: AsyncEngine) -> None:
    """Handle ensure schema.

    Args:
        - engine: AsyncEngine - The engine value.

    Returns:
        - return None - The return value.
    """
    schema_key = str(engine.url)
    if schema_key in _SCHEMA_INITIALIZED:
        return

    async with _STORAGE_INIT_LOCK:
        if schema_key in _SCHEMA_INITIALIZED:
            return

        with observed_span("sql", "sql.schema_init"):
            async with engine.begin() as conn:
                await conn.run_sync(APP_MEMORY_METADATA.create_all)

        _SCHEMA_INITIALIZED.add(schema_key)


async def ensure_storage_ready(settings: Settings | None = None) -> None:
    """Create app SQL tables once per process, preferably during startup.

    Args:
        - settings: Settings | None - The settings value.

    Returns:
        - return None - The return value.
    """
    settings = settings or get_settings()
    await _ensure_schema(_get_engine(settings))


def _block_type(block: Any) -> str | None:
    """Handle block type.

    Args:
        - block: Any - The block value.

    Returns:
        - return str | None - The return value.
    """
    if isinstance(block, dict):
        return block.get("type")
    return getattr(block, "type", None)


def _block_id(block: Any) -> str | None:
    """Handle block id.

    Args:
        - block: Any - The block value.

    Returns:
        - return str | None - The return value.
    """
    if isinstance(block, dict):
        return block.get("id")
    return getattr(block, "id", None)


def _content_text(content: Any) -> str:
    """Handle content text.

    Args:
        - content: Any - The content value.

    Returns:
        - return str - The return value.
    """
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
    """Handle truncate.

    Args:
        - text: str - The text value.
        - limit: int - The limit value.

    Returns:
        - return str - The return value.
    """
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return f"{text[: limit - 3]}..."


def _summarize_json_payload(payload: dict[str, Any]) -> str:
    """Handle summarize json payload.

    Args:
        - payload: dict[str, Any] - The payload value.

    Returns:
        - return str - The return value.
    """
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
    """Handle summarize tool output.

    Args:
        - output: Any - The output value.

    Returns:
        - return str - The return value.
    """
    text = _content_text(output)
    try:
        parsed = json.loads(text)
    except (TypeError, json.JSONDecodeError):
        return _truncate(text)

    if isinstance(parsed, dict):
        return _summarize_json_payload(parsed)
    return _truncate(json.dumps(parsed, ensure_ascii=False, default=str))


def _compact_tool_result_msg(msg: Msg) -> Msg:
    """Store readable tool-result summaries, not large raw tool payloads.

    Args:
        - msg: Msg - The msg value.

    Returns:
        - return Msg - The return value.
    """
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
    """Keep tool calls and tool results in separate Msg records.

    Args:
        - msg: Msg - The msg value.

    Returns:
        - return list[Msg] - The return value.
    """
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


def _missing_tool_pair_ids(rows: list[dict[str, Any]]) -> set[str]:
    """Return tool ids whose call/result pair is only partially selected.

    Args:
        - rows: list[dict[str, Any]] - Selected message rows.

    Returns:
        - return set[str] - Missing tool call ids.
    """
    call_ids = {
        row.get("tool_call_id")
        for row in rows
        if TOOL_CALL_MARK in set(row.get("marks") or []) and row.get("tool_call_id")
    }
    result_ids = {
        row.get("tool_call_id")
        for row in rows
        if TOOL_RESULT_MARK in set(row.get("marks") or []) and row.get("tool_call_id")
    }
    return (call_ids ^ result_ids) - {None}


def _tool_name(block: Any) -> str | None:
    """Handle tool name.

    Args:
        - block: Any - The block value.

    Returns:
        - return str | None - The return value.
    """
    if isinstance(block, dict):
        return block.get("name")
    return getattr(block, "name", None)


def _message_type(msg: Msg) -> str:
    """Handle message type.

    Args:
        - msg: Msg - The msg value.

    Returns:
        - return str - The return value.
    """
    if msg.metadata.get("kind") == SUMMARY_MARK:
        return SUMMARY_MARK
    if msg.has_content_blocks("tool_use"):
        return TOOL_CALL_MARK
    if msg.has_content_blocks("tool_result"):
        return TOOL_RESULT_MARK
    return msg.role


def _compact_content(msg: Msg) -> str:
    """Handle compact content.

    Args:
        - msg: Msg - The msg value.

    Returns:
        - return str - The return value.
    """
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
        prompt_scope: str = "conversation",
    ) -> None:
        """Initialize the instance.

        Args:
            - engine: AsyncEngine - The engine value.
            - user_id: str - The user id value.
            - session_id: str - The session id value.
            - keep_recent: int - The keep recent value.
            - prompt_scope: str - Prompt memory scope.

        Returns:
            - return None - The return value.
        """
        super().__init__()
        self.engine = engine
        self.user_id = user_id
        self.session_id = session_id
        self.conversation_id = _storage_session_id(user_id, session_id)
        self.keep_recent = keep_recent
        self.prompt_scope = prompt_scope
        self._summary_hydrated = False
        self._initialized = False
        self._lock = asyncio.Lock()
        self._capture_turn_messages = False
        self._turn_messages: list[Msg] = []

    def begin_turn_capture(self) -> None:
        """Begin capturing messages added during the current agent turn.

        Returns:
            - return None - This function has no return value.
        """
        self._capture_turn_messages = True
        self._turn_messages = []

    def consume_turn_capture(self) -> list[Msg]:
        """Return messages captured during the current agent turn.

        Returns:
            - return list[Msg] - The captured messages.
        """
        messages = list(self._turn_messages)
        self._turn_messages = []
        self._capture_turn_messages = False
        return messages

    async def _create_table(self) -> None:
        """Handle create table.

        Returns:
            - return None - The return value.
        """
        if self._initialized:
            return

        await _ensure_schema(self.engine)
        conversation_key = (self.user_id, self.conversation_id)
        if conversation_key in _ENSURED_CONVERSATIONS:
            self._initialized = True
            return

        async with _STORAGE_INIT_LOCK:
            if conversation_key in _ENSURED_CONVERSATIONS:
                self._initialized = True
                return

            with observed_span("sql", "sql.ensure_conversation"):
                async with self.engine.begin() as conn:
                    dialect_name = conn.dialect.name
                    await conn.execute(
                        _insert_ignore(
                            USERS_TABLE,
                            {
                                "id": self.user_id,
                                "external_user_id": self.user_id,
                                "metadata": {},
                            },
                            dialect_name,
                        )
                    )
                    await conn.execute(
                        _insert_ignore(
                            CONVERSATIONS_TABLE,
                            {
                                "id": self.conversation_id,
                                "user_id": self.user_id,
                                "session_id": self.session_id,
                                "status": "active",
                                "last_sequence_no": 0,
                                "last_compressed_sequence_no": 0,
                                "metadata": {},
                            },
                            dialect_name,
                        )
                    )

            _ENSURED_CONVERSATIONS.add(conversation_key)
            self._initialized = True

    async def _hydrate_summary(self) -> None:
        """Handle hydrate summary.

        Returns:
            - return None - The return value.
        """
        if self._summary_hydrated:
            return

        with observed_span("memory", "memory.summary_cache") as span:
            cached = await get_cached_summary(self.user_id, self.session_id)
            span.update(hit=cached.found)
        if cached.found:
            self._compressed_summary = cached.summary
            self._summary_hydrated = True
            return

        await self._create_table()
        with observed_span("sql", "sql.hydrate_summary"):
            async with self.engine.connect() as conn:
                row = (
                    await conn.execute(
                        select(CONVERSATION_SUMMARIES_TABLE.c.summary)
                        .where(
                            CONVERSATION_SUMMARIES_TABLE.c.conversation_id
                            == self.conversation_id,
                            CONVERSATION_SUMMARIES_TABLE.c.is_active.is_(True),
                        )
                        .order_by(CONVERSATION_SUMMARIES_TABLE.c.summary_version.desc())
                        .limit(1)
                    )
                ).first()

        self._compressed_summary = row.summary if row else ""
        self._summary_hydrated = True
        await set_cached_summary(
            self.user_id,
            self.session_id,
            self._compressed_summary,
        )

    async def _reserve_sequences(self, conn, count: int) -> int:
        """Reserve sequence numbers for a batch insert.

        Args:
            - conn: Any - The conn value.
            - count: int - Number of sequence numbers to reserve.

        Returns:
            - return int - The return value.
        """
        if count <= 0:
            return 0

        stmt = select(CONVERSATIONS_TABLE.c.last_sequence_no).where(
            CONVERSATIONS_TABLE.c.id == self.conversation_id
        )
        if conn.dialect.name != "sqlite":
            stmt = stmt.with_for_update()

        current = await conn.scalar(stmt)
        if current is None:
            current = (
                await conn.scalar(
                    select(func.max(CONVERSATION_MESSAGES_TABLE.c.sequence_no)).where(
                        CONVERSATION_MESSAGES_TABLE.c.conversation_id
                        == self.conversation_id
                    )
                )
                or 0
            )
        next_last = int(current or 0) + count
        await conn.execute(
            update(CONVERSATIONS_TABLE)
            .where(CONVERSATIONS_TABLE.c.id == self.conversation_id)
            .values(last_sequence_no=next_last, updated_at=func.now())
        )
        return int(current or 0) + 1

    async def _fetch_message_rows(
        self,
        *,
        only_uncompressed: bool = False,
        msg_ids: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Handle fetch message rows.

        Args:
            - only_uncompressed: bool - Whether to exclude compressed messages.
            - msg_ids: list[str] | None - Optional message ids to fetch.

        Returns:
            - return list[dict[str, Any]] - The return value.
        """
        await self._create_table()
        with observed_span("sql", "sql.fetch_messages") as span:
            async with self.engine.connect() as conn:
                stmt = select(CONVERSATION_MESSAGES_TABLE).where(
                    CONVERSATION_MESSAGES_TABLE.c.conversation_id
                    == self.conversation_id
                )
                if only_uncompressed:
                    stmt = stmt.where(
                        CONVERSATION_MESSAGES_TABLE.c.is_compressed.is_(False)
                    )
                if msg_ids is not None:
                    stmt = stmt.where(CONVERSATION_MESSAGES_TABLE.c.id.in_(msg_ids))
                stmt = stmt.order_by(CONVERSATION_MESSAGES_TABLE.c.sequence_no)
                rows = (await conn.execute(stmt)).mappings().all()
            span.update(row_count=len(rows))
        return [dict(row) for row in rows]

    async def _fetch_recent_uncompressed_rows(self) -> list[dict[str, Any]]:
        """Fetch the recent uncompressed prompt window.

        Returns:
            - return list[dict[str, Any]] - Recent rows in sequence order.
        """
        await self._create_table()
        with observed_span("sql", "sql.fetch_recent_messages") as span:
            async with self.engine.connect() as conn:
                rows = (
                    (
                        await conn.execute(
                            select(CONVERSATION_MESSAGES_TABLE)
                            .where(
                                CONVERSATION_MESSAGES_TABLE.c.conversation_id
                                == self.conversation_id,
                                CONVERSATION_MESSAGES_TABLE.c.is_compressed.is_(False),
                            )
                            .order_by(CONVERSATION_MESSAGES_TABLE.c.sequence_no.desc())
                            .limit(self.keep_recent)
                        )
                    )
                    .mappings()
                    .all()
                )
                selected = [dict(row) for row in reversed(rows)]
                missing_ids = _missing_tool_pair_ids(selected)
                if missing_ids:
                    pair_rows = (
                        (
                            await conn.execute(
                                select(CONVERSATION_MESSAGES_TABLE).where(
                                    CONVERSATION_MESSAGES_TABLE.c.conversation_id
                                    == self.conversation_id,
                                    CONVERSATION_MESSAGES_TABLE.c.is_compressed.is_(
                                        False
                                    ),
                                    CONVERSATION_MESSAGES_TABLE.c.tool_call_id.in_(
                                        sorted(missing_ids)
                                    ),
                                )
                            )
                        )
                        .mappings()
                        .all()
                    )
                    by_id = {row["id"]: dict(row) for row in selected}
                    by_id.update({row["id"]: dict(row) for row in pair_rows})
                    selected = sorted(
                        by_id.values(),
                        key=lambda row: row["sequence_no"],
                    )
            span.update(row_count=len(selected))
        return selected

    async def _fetch_sequence_rows(
        self,
        *,
        start_sequence: int,
        end_sequence: int,
    ) -> list[dict[str, Any]]:
        """Fetch message rows inside a sequence range.

        Args:
            - start_sequence: int - First sequence number to include.
            - end_sequence: int - Last sequence number to include.

        Returns:
            - return list[dict[str, Any]] - Rows in sequence order.
        """
        await self._create_table()
        with observed_span("sql", "sql.fetch_summary_checkpoint") as span:
            async with self.engine.connect() as conn:
                rows = (
                    (
                        await conn.execute(
                            select(CONVERSATION_MESSAGES_TABLE)
                            .where(
                                CONVERSATION_MESSAGES_TABLE.c.conversation_id
                                == self.conversation_id,
                                CONVERSATION_MESSAGES_TABLE.c.sequence_no
                                >= start_sequence,
                                CONVERSATION_MESSAGES_TABLE.c.sequence_no
                                <= end_sequence,
                            )
                            .order_by(CONVERSATION_MESSAGES_TABLE.c.sequence_no)
                        )
                    )
                    .mappings()
                    .all()
                )
            span.update(row_count=len(rows))
        return [dict(row) for row in rows]

    async def next_summary_checkpoint(
        self,
        checkpoint_size: int,
    ) -> SummaryCheckpoint | None:
        """Return the next fixed-size chunk ready for cumulative summary.

        Args:
            - checkpoint_size: int - Number of new messages per checkpoint.

        Returns:
            - return SummaryCheckpoint | None - Pending checkpoint, if ready.
        """
        checkpoint_size = max(1, checkpoint_size)
        await self._create_table()
        await self._hydrate_summary()
        with observed_span("sql", "sql.summary_checkpoint_state"):
            async with self.engine.connect() as conn:
                row = (
                    await conn.execute(
                        select(
                            CONVERSATIONS_TABLE.c.last_sequence_no,
                            CONVERSATIONS_TABLE.c.last_compressed_sequence_no,
                        ).where(CONVERSATIONS_TABLE.c.id == self.conversation_id)
                    )
                ).first()

        if row is None:
            return None

        last_sequence = int(row.last_sequence_no or 0)
        last_summarized = int(row.last_compressed_sequence_no or 0)
        start_sequence = last_summarized + 1
        end_sequence = last_summarized + checkpoint_size
        if last_sequence < end_sequence:
            return None

        rows = await self._fetch_sequence_rows(
            start_sequence=start_sequence,
            end_sequence=end_sequence,
        )
        missing_ids = _missing_tool_pair_ids(rows)
        if missing_ids:
            with observed_span("sql", "sql.expand_summary_tool_pairs"):
                async with self.engine.connect() as conn:
                    pair_rows = (
                        (
                            await conn.execute(
                                select(CONVERSATION_MESSAGES_TABLE).where(
                                    CONVERSATION_MESSAGES_TABLE.c.conversation_id
                                    == self.conversation_id,
                                    CONVERSATION_MESSAGES_TABLE.c.tool_call_id.in_(
                                        sorted(missing_ids)
                                    ),
                                )
                            )
                        )
                        .mappings()
                        .all()
                    )
            by_id = {row["id"]: dict(row) for row in rows}
            by_id.update({row["id"]: dict(row) for row in pair_rows})
            rows = sorted(by_id.values(), key=lambda value: value["sequence_no"])
            end_sequence = max(int(row["sequence_no"]) for row in rows)

        rows = [
            row
            for row in rows
            if start_sequence <= int(row["sequence_no"]) <= end_sequence
        ]
        if not rows:
            return None

        return SummaryCheckpoint(
            previous_summary=self._compressed_summary,
            messages=tuple(self._msg_from_row(row) for row in rows),
            message_ids=tuple(str(row["id"]) for row in rows),
            source_message_start=start_sequence,
            source_message_end=end_sequence,
        )

    def _msg_from_row(self, row: dict[str, Any]) -> Msg:
        """Handle msg from row.

        Args:
            - row: dict[str, Any] - The row value.

        Returns:
            - return Msg - The return value.
        """
        msg = Msg.from_dict(row["content"])
        msg.id = row["id"]
        return msg

    def _active_summary_msg(self) -> Msg | None:
        """Handle active summary msg.

        Returns:
            - return Msg | None - The return value.
        """
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
        """Handle filter rows.

        Args:
            - rows: list[dict[str, Any]] - The rows value.
            - mark: str | None - The mark value.
            - exclude_mark: str | None - The exclude mark value.

        Returns:
            - return list[dict[str, Any]] - The return value.
        """
        filtered = []
        for row in rows:
            marks = set(row.get("marks") or [])
            if mark is not None and mark not in marks:
                continue
            if exclude_mark is not None and exclude_mark in marks:
                continue
            filtered.append(row)
        return filtered

    async def update_compressed_summary(
        self,
        summary: str,
        *,
        source_message_start: int | None = None,
        source_message_end: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        # AgentScope keeps the active summary on the memory object; this also
        # persists it for restarts and UI history.
        """Handle update compressed summary.

        Args:
            - summary: str - The summary value.
            - source_message_start: int | None - First summarized sequence.
            - source_message_end: int | None - Last summarized sequence.
            - metadata: dict[str, Any] | None - Extra summary metadata.

        Returns:
            - return None - The return value.
        """
        await self._create_table()
        await super().update_compressed_summary(summary)
        with observed_span("sql", "sql.update_summary"):
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
                                func.max(CONVERSATION_SUMMARIES_TABLE.c.summary_version)
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
                            source_message_start=source_message_start,
                            source_message_end=source_message_end,
                            is_active=True,
                            metadata={"kind": SUMMARY_MARK, **(metadata or {})},
                        )
                    )
        self._summary_hydrated = True
        await set_cached_summary(self.user_id, self.session_id, summary)

    async def store_summary_checkpoint(
        self,
        summary: str,
        checkpoint: SummaryCheckpoint,
    ) -> None:
        """Persist a cumulative summary checkpoint and mark its messages.

        Args:
            - summary: str - The cumulative summary text.
            - checkpoint: SummaryCheckpoint - The checkpoint that was summarized.

        Returns:
            - return None - This function has no return value.
        """
        await self.update_compressed_summary(
            summary,
            source_message_start=checkpoint.source_message_start,
            source_message_end=checkpoint.source_message_end,
            metadata={
                "checkpoint": True,
                "message_count": len(checkpoint.message_ids),
            },
        )
        await self.update_messages_mark(
            COMPRESSED_MARK,
            msg_ids=list(checkpoint.message_ids),
        )

    async def clear(self) -> None:
        """Handle clear.

        Returns:
            - return None - The return value.
        """
        with observed_span("sql", "sql.memory_clear"):
            async with self._lock:
                await self._create_table()
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
                    await conn.execute(
                        update(CONVERSATIONS_TABLE)
                        .where(CONVERSATIONS_TABLE.c.id == self.conversation_id)
                        .values(
                            last_sequence_no=0,
                            last_compressed_sequence_no=0,
                            updated_at=func.now(),
                        )
                    )
        self._compressed_summary = ""
        self._summary_hydrated = True
        await set_cached_summary(self.user_id, self.session_id, "")

    async def delete_by_mark(self, mark: str | list[str], **kwargs: Any) -> int:
        """Handle delete by mark.

        Args:
            - mark: str | list[str] - The mark value.
            - kwargs: Any - The kwargs value.

        Returns:
            - return int - The return value.
        """
        await self._create_table()
        marks = [mark] if isinstance(mark, str) else mark
        if SUMMARY_MARK in marks:
            with observed_span("sql", "sql.delete_summary_mark"):
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
            await set_cached_summary(self.user_id, self.session_id, "")

        rows = await self._fetch_message_rows()
        msg_ids = [
            row["id"] for row in rows if set(row.get("marks") or []).intersection(marks)
        ]
        return await self.delete(msg_ids)

    async def get_uncompressed_messages(self) -> list[Msg]:
        """Return full uncompressed DB messages, excluding the summary marker.

        Returns:
            - return list[Msg] - The return value.
        """
        rows = self._filter_rows(
            await self._fetch_message_rows(only_uncompressed=True),
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
        """Return the memory.

        Args:
            - mark: str | None - The mark value.
            - exclude_mark: str | None - The exclude mark value.
            - prepend_summary: bool - The prepend summary value.
            - kwargs: Any - The kwargs value.

        Returns:
            - return list[Msg] - The return value.
        """
        if (
            self.prompt_scope == "current_turn"
            and mark is None
            and exclude_mark is None
        ):
            return list(self._turn_messages) if self._capture_turn_messages else []

        await self._hydrate_summary()

        if mark == SUMMARY_MARK:
            summary = self._active_summary_msg()
            return [summary] if summary else []

        if exclude_mark == COMPRESSED_MARK:
            rows = self._filter_rows(
                await self._fetch_recent_uncompressed_rows(),
                mark,
                exclude_mark,
            )
        else:
            rows = self._filter_rows(
                await self._fetch_message_rows(
                    only_uncompressed=exclude_mark == COMPRESSED_MARK,
                ),
                mark,
                exclude_mark,
            )
        msgs = [self._msg_from_row(row) for row in rows]

        # Normal prompt construction receives summary + recent window only.
        if exclude_mark == COMPRESSED_MARK:
            if prepend_summary and self._compressed_summary:
                return [Msg("memory", self._compressed_summary, "user"), *msgs]
            return msgs

        if prepend_summary and self._compressed_summary:
            return [Msg("memory", self._compressed_summary, "user"), *msgs]
        return msgs

    async def add(
        self,
        memories: Msg | list[Msg] | None,
        marks: str | list[str] | None = None,
        **kwargs: Any,
    ) -> None:
        """Handle add.

        Args:
            - memories: Msg | list[Msg] | None - The memories value.
            - marks: str | list[str] | None - The marks value.
            - kwargs: Any - The kwargs value.

        Returns:
            - return None - The return value.
        """
        if memories is None:
            return

        if isinstance(memories, Msg):
            memories = [memories]

        base_marks = [marks] if isinstance(marks, str) else list(marks or [])
        skip_duplicated = kwargs.get("skip_duplicated", True)
        prepared_messages: list[Msg] = []
        for original in memories:
            for msg in _split_mixed_tool_messages(original):
                prepared_messages.append(_compact_tool_result_msg(msg))

        if not prepared_messages:
            return

        with observed_span("sql", "sql.memory_add") as span:
            async with self._lock:
                await self._create_table()
                async with self.engine.begin() as conn:
                    existing_ids: set[str] = set()
                    if skip_duplicated:
                        existing_ids = {
                            row[0]
                            for row in (
                                await conn.execute(
                                    select(CONVERSATION_MESSAGES_TABLE.c.id).where(
                                        CONVERSATION_MESSAGES_TABLE.c.id.in_(
                                            [msg.id for msg in prepared_messages]
                                        )
                                    )
                                )
                            ).all()
                        }

                    rows_to_insert: list[dict[str, Any]] = []
                    title_candidate = None
                    captured: list[Msg] = []
                    sequence_no = await self._reserve_sequences(
                        conn,
                        len(
                            [
                                msg
                                for msg in prepared_messages
                                if msg.id not in existing_ids
                            ]
                        ),
                    )
                    for msg in prepared_messages:
                        if skip_duplicated and msg.id in existing_ids:
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
                        if visible and msg.role == "user" and not title_candidate:
                            title_candidate = compact
                        rows_to_insert.append(
                            {
                                "id": msg.id,
                                "conversation_id": self.conversation_id,
                                "sequence_no": sequence_no,
                                "role": msg.role,
                                "name": msg.name,
                                "message_type": _message_type(msg),
                                "content": msg.to_dict(),
                                "compact_content": compact,
                                "tool_call_id": (
                                    _block_id(tool_block) if tool_block else None
                                ),
                                "tool_name": (
                                    _tool_name(tool_block) if tool_block else None
                                ),
                                "marks": sorted(set(msg_marks)),
                                "visible_to_user": visible,
                                "is_compressed": COMPRESSED_MARK in set(msg_marks),
                                "metadata": msg.metadata,
                            }
                        )
                        captured.append(msg)
                        sequence_no += 1

                    if rows_to_insert:
                        await conn.execute(
                            _insert_many_ignore(
                                CONVERSATION_MESSAGES_TABLE,
                                rows_to_insert,
                                conn.dialect.name,
                            )
                        )
                    if rows_to_insert:
                        values = {"updated_at": func.now()}
                        if title_candidate:
                            values["title"] = func.coalesce(
                                CONVERSATIONS_TABLE.c.title,
                                _truncate(title_candidate, 80),
                            )
                        await conn.execute(
                            update(CONVERSATIONS_TABLE)
                            .where(CONVERSATIONS_TABLE.c.id == self.conversation_id)
                            .values(**values)
                        )
                    if self._capture_turn_messages:
                        self._turn_messages.extend(captured)
                    span.update(inserted_count=len(rows_to_insert))

    async def delete(self, msg_ids: list[str], **kwargs: Any) -> int:
        """Handle delete.

        Args:
            - msg_ids: list[str] - The msg ids value.
            - kwargs: Any - The kwargs value.

        Returns:
            - return int - The return value.
        """
        if not msg_ids:
            return 0
        await self._create_table()
        with observed_span("sql", "sql.memory_delete") as span:
            async with self._lock:
                async with self.engine.begin() as conn:
                    result = await conn.execute(
                        delete(CONVERSATION_MESSAGES_TABLE).where(
                            CONVERSATION_MESSAGES_TABLE.c.conversation_id
                            == self.conversation_id,
                            CONVERSATION_MESSAGES_TABLE.c.id.in_(msg_ids),
                        )
                    )
            deleted_count = result.rowcount or 0
            span.update(deleted_count=deleted_count)
            return deleted_count

    async def size(self) -> int:
        """Handle size.

        Returns:
            - return int - The return value.
        """
        await self._create_table()
        with observed_span("sql", "sql.memory_size"):
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
        """Handle update messages mark.

        Args:
            - new_mark: str | None - The new mark value.
            - old_mark: str | None - The old mark value.
            - msg_ids: list[str] | None - The msg ids value.

        Returns:
            - return int - The return value.
        """
        await self._create_table()
        rows = await self._fetch_message_rows(msg_ids=msg_ids)
        updated = 0
        max_compressed_sequence: int | None = None
        with observed_span("sql", "sql.update_message_marks") as span:
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
                            values = {"marks": sorted(set(marks))}
                            if new_mark == COMPRESSED_MARK:
                                values["is_compressed"] = True
                                max_compressed_sequence = max(
                                    max_compressed_sequence or 0,
                                    int(row["sequence_no"]),
                                )
                            elif old_mark == COMPRESSED_MARK:
                                values["is_compressed"] = False
                            await conn.execute(
                                update(CONVERSATION_MESSAGES_TABLE)
                                .where(CONVERSATION_MESSAGES_TABLE.c.id == row["id"])
                                .values(**values)
                            )
                            updated += 1
                    if max_compressed_sequence is not None:
                        await conn.execute(
                            update(CONVERSATIONS_TABLE)
                            .where(CONVERSATIONS_TABLE.c.id == self.conversation_id)
                            .values(
                                last_compressed_sequence_no=max_compressed_sequence,
                                updated_at=func.now(),
                            )
                        )
            span.update(updated_count=updated)
        return updated

    async def close(self) -> None:
        """Handle close.

        Returns:
            - return None - The return value.
        """
        return None


def load_memory(
    session_id: str,
    user_id: str = DEFAULT_USER_ID,
    settings: Settings | None = None,
    prompt_scope: str = "conversation",
) -> AppSQLMemory:
    """Create SQL-backed memory keyed by user_id and session_id.

    Args:
        - session_id: str - The session id value.
        - user_id: str - The user id value.
        - settings: Settings | None - The settings value.
        - prompt_scope: str - Prompt memory scope.

    Returns:
        - return AppSQLMemory - The return value.
    """
    settings = settings or get_settings()
    return AppSQLMemory(
        _get_engine(settings),
        user_id=user_id,
        session_id=session_id,
        keep_recent=_window_size(settings),
        prompt_scope=prompt_scope,
    )


async def list_user_conversations(
    user_id: str = DEFAULT_USER_ID,
    *,
    limit: int = 20,
    settings: Settings | None = None,
) -> list[dict[str, Any]]:
    """Return recent SQL conversations for the frontend sidebar.

    Args:
        - user_id: str - The user id value.
        - limit: int - The limit value.
        - settings: Settings | None - The settings value.

    Returns:
        - return list[dict[str, Any]] - The return value.
    """
    settings = settings or get_settings()
    engine = _get_engine(settings)
    await ensure_storage_ready(settings)

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

    with observed_span("sql", "sql.list_conversations") as span:
        async with engine.connect() as conn:
            rows = (await conn.execute(query)).mappings().all()
        span.update(row_count=len(rows))

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
    """Return visible user/assistant messages for one conversation.

    Args:
        - session_id: str - The session id value.
        - user_id: str - The user id value.
        - limit: int - The limit value.
        - settings: Settings | None - The settings value.

    Returns:
        - return list[dict[str, Any]] - The return value.
    """
    settings = settings or get_settings()
    engine = _get_engine(settings)
    conversation_id = _storage_session_id(user_id, session_id)

    await ensure_storage_ready(settings)
    with observed_span("sql", "sql.list_messages") as span:
        async with engine.connect() as conn:
            rows = (
                (
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
                            CONVERSATION_MESSAGES_TABLE.c.conversation_id
                            == conversation_id,
                            CONVERSATION_MESSAGES_TABLE.c.visible_to_user.is_(True),
                        )
                        .order_by(CONVERSATION_MESSAGES_TABLE.c.sequence_no)
                        .limit(limit)
                    )
                )
                .mappings()
                .all()
            )
        span.update(row_count=len(rows))

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
    """Seed SQL menu_items from the canonical parsed menu document.

    Args:
        - settings: Settings | None - The settings value.

    Returns:
        - return None - The return value.
    """
    global _MENU_CATALOG_INITIALIZED
    if _MENU_CATALOG_INITIALIZED:
        return

    settings = settings or get_settings()
    engine = _get_engine(settings)
    await ensure_storage_ready(settings)

    with observed_span("sql", "sql.ensure_menu_catalog") as span:
        async with _STORAGE_INIT_LOCK:
            if _MENU_CATALOG_INITIALIZED:
                span.update(cached=True, seeded_count=0)
                return

            async with engine.begin() as conn:
                existing_count = await conn.scalar(
                    select(func.count(MENU_ITEMS_TABLE.c.id))
                )
                if existing_count:
                    _MENU_CATALOG_INITIALIZED = True
                    span.update(existing_count=int(existing_count), seeded_count=0)
                    return

                seeded_count = 0
                for item in build_menu_item_match_index():
                    price = _first_price_inr(item.price)
                    if price is None:
                        continue
                    await conn.execute(
                        _insert_ignore(
                            MENU_ITEMS_TABLE,
                            {
                                "id": _menu_item_id(item.name),
                                "name": item.name,
                                "normalized_name": _normalized_menu_name(item.name),
                                "top_level": item.top_level,
                                "section": item.section,
                                "price_inr": price,
                                "serving": item.serving,
                                "dietary_tags": item.dietary_tags,
                                "tags": list(item.tags),
                                "description": item.description,
                                "available": True,
                                "metadata": {"source": "BTB_Menu_Enhanced.md"},
                            },
                            conn.dialect.name,
                        )
                    )
                    seeded_count += 1
                _MENU_CATALOG_INITIALIZED = True
                span.update(existing_count=0, seeded_count=seeded_count)


async def resolve_menu_item_for_cart(
    item_ref: str,
    settings: Settings | None = None,
) -> MenuItem:
    """Resolve an exact SQL menu item id or exact item name for cart tools.

    Args:
        - item_ref: str - The item ref value.
        - settings: Settings | None - The settings value.

    Returns:
        - return MenuItem - The return value.
    """
    settings = settings or get_settings()
    await ensure_menu_catalog(settings)
    engine = _get_engine(settings)
    normalized = _normalized_menu_name(item_ref)

    with observed_span("sql", "sql.resolve_menu_item") as span:
        async with engine.connect() as conn:
            row = (
                (
                    await conn.execute(
                        select(MENU_ITEMS_TABLE).where(
                            (MENU_ITEMS_TABLE.c.id == item_ref)
                            | (MENU_ITEMS_TABLE.c.normalized_name == normalized)
                        )
                    )
                )
                .mappings()
                .first()
            )
        span.update(found=row is not None)

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
    """Persist the latest current-cart snapshot for a conversation.

    Args:
        - session_id: str - The session id value.
        - cart: Cart - The cart value.
        - user_id: str - The user id value.
        - settings: Settings | None - The settings value.

    Returns:
        - return None - The return value.
    """
    memory = load_memory(session_id, user_id=user_id, settings=settings)
    await memory._create_table()

    cart_id = _cart_id(memory.conversation_id)
    with observed_span("sql", "sql.save_cart_snapshot") as span:
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
                    delete(CART_ITEMS_TABLE).where(
                        CART_ITEMS_TABLE.c.cart_id == cart_id
                    )
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
        span.update(item_count=len(cart.items), total_inr=cart.total_inr)


async def clear_cart_snapshot(
    session_id: str,
    user_id: str = DEFAULT_USER_ID,
    settings: Settings | None = None,
) -> None:
    """Persist an empty current cart after checkout or manual clear.

    Args:
        - session_id: str - The session id value.
        - user_id: str - The user id value.
        - settings: Settings | None - The settings value.

    Returns:
        - return None - The return value.
    """
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
    """Delete persisted memory, cart, and order data for one user/session.

    Args:
        - session_id: str - The session id value.
        - user_id: str - The user id value.
        - settings: Settings | None - The settings value.

    Returns:
        - return None - The return value.
    """
    settings = settings or get_settings()
    engine = _get_engine(settings)
    conversation_id = _storage_session_id(user_id, session_id)

    await ensure_storage_ready(settings)
    with observed_span("sql", "sql.delete_session"):
        async with engine.begin() as conn:
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
                    delete(CART_ITEMS_TABLE).where(
                        CART_ITEMS_TABLE.c.cart_id.in_(cart_ids)
                    )
                )
            await conn.execute(
                delete(CARTS_TABLE).where(
                    CARTS_TABLE.c.conversation_id == conversation_id
                )
            )

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
                delete(CONVERSATIONS_TABLE).where(
                    CONVERSATIONS_TABLE.c.id == conversation_id
                )
            )
    _ENSURED_CONVERSATIONS.discard((user_id, conversation_id))
    await delete_cached_summary(user_id, session_id)


async def save_order_snapshot(
    order: Order,
    user_id: str = DEFAULT_USER_ID,
    settings: Settings | None = None,
) -> None:
    """Persist or update an order and its immutable line-item snapshot.

    Args:
        - order: Order - The order value.
        - user_id: str - The user id value.
        - settings: Settings | None - The settings value.

    Returns:
        - return None - The return value.
    """
    memory = load_memory(order.session_id, user_id=user_id, settings=settings)
    await memory._create_table()

    with observed_span("sql", "sql.save_order_snapshot") as span:
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
        span.update(item_count=len(order.items), total_inr=order.total_inr)


async def get_summary(memory: MemoryBase) -> Msg | None:
    """Return the summary.

    Args:
        - memory: MemoryBase - The memory value.

    Returns:
        - return Msg | None - The return value.
    """
    summaries = await memory.get_memory(mark=SUMMARY_MARK, prepend_summary=False)
    return summaries[-1] if summaries else None


async def get_recent_messages(memory: MemoryBase) -> list[Msg]:
    """Return the recent messages.

    Args:
        - memory: MemoryBase - The memory value.

    Returns:
        - return list[Msg] - The return value.
    """
    return await memory.get_memory(
        exclude_mark=COMPRESSED_MARK,
        prepend_summary=False,
    )


async def build_context(memory: MemoryBase) -> list[Msg]:
    """Return prompt messages in the required order before the current input.

    Args:
        - memory: MemoryBase - The memory value.

    Returns:
        - return list[Msg] - The return value.
    """
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
    """Save the messages.

    Args:
        - memory: MemoryBase - The memory value.
        - msgs: Msg | list[Msg] - The msgs value.
        - marks: str | list[str] | None - The marks value.

    Returns:
        - return None - The return value.
    """
    await memory.add(msgs, marks=marks)


def _message_text(msg: Msg) -> str:
    """Return best-effort text content for summary checkpoints.

    Args:
        - msg: Msg - The message value.

    Returns:
        - return str - Text content.
    """
    get_text = getattr(msg, "get_text_content", None)
    if callable(get_text):
        return get_text() or ""
    return _content_text(getattr(msg, "content", ""))


def _message_for_summary(msg: Msg) -> str:
    """Return one compact line for checkpoint summarization.

    Args:
        - msg: Msg - The message to render.

    Returns:
        - return str - Compact role-prefixed text.
    """
    role = getattr(msg, "role", "unknown")
    name = getattr(msg, "name", role)
    text = _message_text(msg).strip()
    if not text:
        text = _compact_content(msg)
    text = _truncate(text, 1200)
    return f"{role}/{name}: {text}"


def _checkpoint_prompt(checkpoint: SummaryCheckpoint) -> str:
    """Build the LLM prompt for one cumulative summary checkpoint.

    Args:
        - checkpoint: SummaryCheckpoint - The checkpoint to summarize.

    Returns:
        - return str - Prompt text for the summary model.
    """
    previous = checkpoint.previous_summary.strip() or "No previous summary yet."
    messages = "\n".join(
        f"{index}. {_message_for_summary(msg)}"
        for index, msg in enumerate(checkpoint.messages, start=1)
    )
    return (
        f"{COMPRESSION_PROMPT}\n\n"
        f"<previous_summary>\n{previous}\n</previous_summary>\n\n"
        f"<new_checkpoint_messages "
        f"start_sequence=\"{checkpoint.source_message_start}\" "
        f"end_sequence=\"{checkpoint.source_message_end}\">\n"
        f"{messages}\n"
        f"</new_checkpoint_messages>"
    )


def _response_text(response: Any) -> str:
    """Extract plain text from an AgentScope chat response.

    Args:
        - response: Any - The model response.

    Returns:
        - return str - Extracted text.
    """
    parts: list[str] = []
    for block in getattr(response, "content", []) or []:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(str(block.get("text", "")))
        elif getattr(block, "type", None) == "text":
            parts.append(str(getattr(block, "text", "")))
    return "\n".join(part for part in parts if part).strip()


async def _generate_checkpoint_summary(
    agent: ReActAgent,
    checkpoint: SummaryCheckpoint,
) -> str:
    """Generate the cumulative summary for one checkpoint with the LLM.

    Args:
        - agent: ReActAgent - The agent that owns the memory/model.
        - checkpoint: SummaryCheckpoint - The pending checkpoint.

    Returns:
        - return str - The formatted cumulative summary.
    """
    custom = getattr(agent, "summarize_memory_checkpoint", None)
    if callable(custom):
        result = custom(checkpoint)
        if hasattr(result, "__await__"):
            result = await result
        return str(result)

    from cafe.agents.llm import make_chat_model
    from cafe.agents.memory import make_chat_formatter

    settings = get_settings()
    model = getattr(agent, "model", None) or make_chat_model(
        settings,
        agent_name="MemorySummarizer",
    )
    formatter = getattr(agent, "formatter", None) or make_chat_formatter(settings)
    prompt = await formatter.format(
        [
            Msg("system", "You write concise cumulative cafe memory summaries.", "system"),
            Msg("user", _checkpoint_prompt(checkpoint), "user"),
        ]
    )

    with observed_span(
        "memory_compression",
        "memory.summary_checkpoint",
        {
            "source_message_start": checkpoint.source_message_start,
            "source_message_end": checkpoint.source_message_end,
        },
    ):
        response = await model(prompt, structured_model=CafeConversationSummary)

    final_response = None
    if getattr(model, "stream", False):
        async for chunk in response:
            final_response = chunk
    else:
        final_response = response

    metadata = getattr(final_response, "metadata", None) or {}
    if metadata:
        return SUMMARY_TEMPLATE.format(**metadata)

    text = _response_text(final_response)
    if text:
        return text
    raise RuntimeError("summary checkpoint LLM did not return summary content")


async def should_compress_memory_after_turn(agent: ReActAgent) -> bool:
    """Return whether the next 8-message summary checkpoint is ready.

    Args:
        - agent: ReActAgent - The agent value.

    Returns:
        - return bool - Whether compression should run.
    """
    memory = getattr(agent, "memory", None)
    if memory is None or not hasattr(memory, "next_summary_checkpoint"):
        return False

    settings = get_settings()
    checkpoint = await memory.next_summary_checkpoint(
        settings.memory_summary_checkpoint_messages,
    )
    return checkpoint is not None


async def compress_memory_after_turn(agent: ReActAgent) -> bool:
    """Create one cumulative summary checkpoint when 8 new messages are ready.

    Args:
        - agent: ReActAgent - The agent value.

    Returns:
        - return bool - The return value.
    """
    memory = getattr(agent, "memory", None)
    if memory is None or not hasattr(memory, "next_summary_checkpoint"):
        return False

    settings = get_settings()
    checkpoint = await memory.next_summary_checkpoint(
        settings.memory_summary_checkpoint_messages,
    )
    if checkpoint is None:
        return False

    summary = await _generate_checkpoint_summary(agent, checkpoint)
    await memory.store_summary_checkpoint(summary, checkpoint)
    return True
