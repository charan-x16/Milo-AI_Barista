"""drop unused memory summary columns

Revision ID: 20260513_0005
Revises: 20260513_0004
Create Date: 2026-05-13 13:15:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260513_0005"
down_revision = "20260513_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index(
        "ix_memory_summaries_user_session_checkpoint",
        table_name="memory_summaries",
    )
    op.drop_index(
        "ix_memory_summaries_conversation_active",
        table_name="memory_summaries",
    )
    with op.batch_alter_table("memory_summaries") as batch_op:
        batch_op.drop_column("session_id")
        batch_op.drop_column("is_active")


def downgrade() -> None:
    with op.batch_alter_table("memory_summaries") as batch_op:
        batch_op.add_column(sa.Column("is_active", sa.Boolean(), nullable=True))
        batch_op.add_column(sa.Column("session_id", sa.String(length=255), nullable=True))

    op.execute("update memory_summaries set is_active = true where is_active is null")
    op.execute("update memory_summaries set session_id = '' where session_id is null")

    with op.batch_alter_table("memory_summaries") as batch_op:
        batch_op.alter_column("is_active", nullable=False)
        batch_op.alter_column("session_id", nullable=False)

    op.create_index(
        "ix_memory_summaries_conversation_active",
        "memory_summaries",
        ["conversation_id", "is_active", "summary_version"],
    )
    op.create_index(
        "ix_memory_summaries_user_session_checkpoint",
        "memory_summaries",
        ["user_id", "session_id", "checkpoint_message_count"],
    )
