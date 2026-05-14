"""SQL repository for checkpoint memory summaries."""

from __future__ import annotations

from typing import Any

from sqlalchemy import delete, insert, select
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.sql.schema import Table

from .models import MemorySummaryInsert


class MemorySummaryRepository:
    def __init__(
        self,
        engine: AsyncEngine,
        *,
        summary_table: Table,
        message_table: Table,
    ) -> None:
        self.engine = engine
        self.summary_table = summary_table
        self.message_table = message_table

    async def latest_summary_text(self, conversation_id: str) -> str:
        async with self.engine.connect() as conn:
            row = (
                await conn.execute(
                    select(self.summary_table.c.summary_text)
                    .where(self.summary_table.c.conversation_id == conversation_id)
                    .order_by(self.summary_table.c.summary_version.desc())
                    .limit(1)
                )
            ).first()

        return row.summary_text if row else ""

    async def latest_summary_data(self, conversation_id: str) -> dict[str, Any] | None:
        """Return the latest structured summary JSON for a conversation."""
        async with self.engine.connect() as conn:
            result = await conn.scalar(
                select(self.summary_table.c.summary_json)
                .where(self.summary_table.c.conversation_id == conversation_id)
                .order_by(self.summary_table.c.summary_version.desc())
                .limit(1)
            )

        if result is None:
            return None
        if isinstance(result, str):
            import json

            try:
                parsed = json.loads(result)
            except json.JSONDecodeError:
                return None
            return parsed if isinstance(parsed, dict) else None
        return result if isinstance(result, dict) else None

    async def latest_summary(
        self,
        conversation_id: str,
    ) -> dict[str, Any] | None:
        async with self.engine.connect() as conn:
            row = (
                await conn.execute(
                    select(self.summary_table)
                    .where(self.summary_table.c.conversation_id == conversation_id)
                    .order_by(self.summary_table.c.summary_version.desc())
                    .limit(1)
                )
            ).mappings().first()

        return dict(row) if row else None

    async def checkpoint_exists(
        self,
        conversation_id: str,
        checkpoint_message_count: int,
    ) -> bool:
        async with self.engine.connect() as conn:
            row = (
                await conn.execute(
                    select(self.summary_table.c.id)
                    .where(
                        self.summary_table.c.conversation_id == conversation_id,
                        self.summary_table.c.checkpoint_message_count
                        == checkpoint_message_count,
                    )
                    .limit(1)
                )
            ).first()

        return row is not None

    async def visible_message_rows(
        self,
        conversation_id: str,
    ) -> list[dict[str, Any]]:
        async with self.engine.connect() as conn:
            rows = (
                await conn.execute(
                    select(
                        self.message_table.c.id,
                        self.message_table.c.sequence_no,
                        self.message_table.c.role,
                        self.message_table.c.name,
                        self.message_table.c.compact_content,
                        self.message_table.c.created_at,
                    )
                    .where(
                        self.message_table.c.conversation_id == conversation_id,
                        self.message_table.c.visible_to_user.is_(True),
                    )
                    .order_by(self.message_table.c.sequence_no)
                )
            ).mappings().all()

        return [dict(row) for row in rows]

    async def insert_summary(self, record: MemorySummaryInsert) -> bool:
        async with self.engine.begin() as conn:
            existing = (
                await conn.execute(
                    select(self.summary_table.c.id)
                    .where(
                        self.summary_table.c.conversation_id == record.conversation_id,
                        self.summary_table.c.checkpoint_message_count
                        == record.checkpoint_message_count,
                    )
                    .limit(1)
                )
            ).first()
            if existing:
                return False

            await conn.execute(
                insert(self.summary_table).values(
                    id=record.id,
                    conversation_id=record.conversation_id,
                    user_id=record.user_id,
                    summary_version=record.summary_version,
                    checkpoint_message_count=record.checkpoint_message_count,
                    source_message_start=record.source_message_start,
                    source_message_end=record.source_message_end,
                    previous_summary_id=record.previous_summary_id,
                    summary_text=record.summary_text,
                    summary_json=record.summary_json,
                    metadata=record.metadata,
                )
            )

        return True

    async def delete_for_conversation(
        self,
        conversation_id: str,
        *,
        conn: Any | None = None,
    ) -> None:
        stmt = delete(self.summary_table).where(
            self.summary_table.c.conversation_id == conversation_id
        )
        if conn is not None:
            await conn.execute(stmt)
            return

        async with self.engine.begin() as conn:
            await conn.execute(stmt)
