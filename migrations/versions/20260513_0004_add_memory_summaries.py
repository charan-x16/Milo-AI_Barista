"""add cumulative memory summaries

Revision ID: 20260513_0004
Revises: 20260508_0004
Create Date: 2026-05-13 12:30:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260513_0004"
down_revision = "20260508_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "memory_summaries",
        sa.Column("id", sa.String(length=255), primary_key=True),
        sa.Column("conversation_id", sa.String(length=255), nullable=False),
        sa.Column("user_id", sa.String(length=255), nullable=False),
        sa.Column("session_id", sa.String(length=255), nullable=False),
        sa.Column("summary_version", sa.Integer(), nullable=False),
        sa.Column("checkpoint_message_count", sa.Integer(), nullable=False),
        sa.Column("source_message_start", sa.Integer(), nullable=False),
        sa.Column("source_message_end", sa.Integer(), nullable=False),
        sa.Column("previous_summary_id", sa.String(length=255), nullable=True),
        sa.Column("summary_text", sa.Text(), nullable=False),
        sa.Column("summary_json", sa.JSON(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"]),
        sa.ForeignKeyConstraint(["previous_summary_id"], ["memory_summaries.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.UniqueConstraint(
            "conversation_id",
            "checkpoint_message_count",
            name="uq_memory_summaries_conversation_checkpoint",
        ),
    )
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


def downgrade() -> None:
    op.drop_index(
        "ix_memory_summaries_user_session_checkpoint",
        table_name="memory_summaries",
    )
    op.drop_index(
        "ix_memory_summaries_conversation_active",
        table_name="memory_summaries",
    )
    op.drop_table("memory_summaries")
