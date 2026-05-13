"""SQL models and typed records for checkpoint memory summaries."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    func,
)


MEMORY_SUMMARY_OVERLAP_MESSAGES = 3


@dataclass(frozen=True)
class MemorySummaryDraft:
    summary_text: str
    summary_json: dict[str, Any]


MemorySummarizer = Callable[
    [str | None, list[dict[str, Any]]],
    Awaitable[MemorySummaryDraft],
]


@dataclass(frozen=True)
class MemorySummaryInsert:
    id: str
    conversation_id: str
    user_id: str
    summary_version: int
    checkpoint_message_count: int
    source_message_start: int
    source_message_end: int
    previous_summary_id: str | None
    summary_text: str
    summary_json: dict[str, Any]
    metadata: dict[str, Any]


def create_memory_summaries_table(metadata: MetaData) -> Table:
    return Table(
        "memory_summaries",
        metadata,
        Column("id", String(255), primary_key=True),
        Column("conversation_id", String(255), ForeignKey("conversations.id"), nullable=False),
        Column("user_id", String(255), ForeignKey("users.id"), nullable=False),
        Column("summary_version", Integer, nullable=False),
        Column("checkpoint_message_count", Integer, nullable=False),
        Column("source_message_start", Integer, nullable=False),
        Column("source_message_end", Integer, nullable=False),
        Column("previous_summary_id", String(255), ForeignKey("memory_summaries.id"), nullable=True),
        Column("summary_text", Text, nullable=False),
        Column("summary_json", JSON, nullable=False, default=dict),
        Column("metadata", JSON, nullable=False, default=dict),
        Column("created_at", DateTime(timezone=True), server_default=func.now()),
        Column("updated_at", DateTime(timezone=True), server_default=func.now()),
        UniqueConstraint(
            "conversation_id",
            "checkpoint_message_count",
            name="uq_memory_summaries_conversation_checkpoint",
        ),
    )
