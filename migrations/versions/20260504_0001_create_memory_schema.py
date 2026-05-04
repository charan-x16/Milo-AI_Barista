"""create memory schema

Revision ID: 20260504_0001
Revises:
Create Date: 2026-05-04 01:35:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260504_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(length=255), primary_key=True),
        sa.Column("external_user_id", sa.String(length=255), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("external_user_id", name="uq_users_external_user_id"),
    )

    op.create_table(
        "conversations",
        sa.Column("id", sa.String(length=255), primary_key=True),
        sa.Column("user_id", sa.String(length=255), nullable=False),
        sa.Column("session_id", sa.String(length=255), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.UniqueConstraint(
            "user_id",
            "session_id",
            name="uq_conversations_user_session",
        ),
    )

    op.create_table(
        "conversation_messages",
        sa.Column("id", sa.String(length=255), primary_key=True),
        sa.Column("conversation_id", sa.String(length=255), nullable=False),
        sa.Column("sequence_no", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("message_type", sa.String(length=32), nullable=False),
        sa.Column("content", sa.JSON(), nullable=False),
        sa.Column("compact_content", sa.Text(), nullable=True),
        sa.Column("tool_call_id", sa.String(length=255), nullable=True),
        sa.Column("tool_name", sa.String(length=255), nullable=True),
        sa.Column("marks", sa.JSON(), nullable=False),
        sa.Column("visible_to_user", sa.Boolean(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"]),
        sa.UniqueConstraint(
            "conversation_id",
            "sequence_no",
            name="uq_conversation_messages_sequence",
        ),
    )

    op.create_table(
        "conversation_summaries",
        sa.Column("id", sa.String(length=255), primary_key=True),
        sa.Column("conversation_id", sa.String(length=255), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("summary_version", sa.Integer(), nullable=False),
        sa.Column("source_message_start", sa.Integer(), nullable=True),
        sa.Column("source_message_end", sa.Integer(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"]),
    )

    op.create_index(
        "ix_conversations_user_created_at",
        "conversations",
        ["user_id", "created_at"],
    )
    op.create_index(
        "ix_conversation_messages_conversation_sequence",
        "conversation_messages",
        ["conversation_id", "sequence_no"],
    )
    op.create_index(
        "ix_conversation_summaries_conversation_active",
        "conversation_summaries",
        ["conversation_id", "is_active"],
    )

def downgrade() -> None:
    op.drop_index(
        "ix_conversation_summaries_conversation_active",
        table_name="conversation_summaries",
    )
    op.drop_index(
        "ix_conversation_messages_conversation_sequence",
        table_name="conversation_messages",
    )
    op.drop_index("ix_conversations_user_created_at", table_name="conversations")
    op.drop_table("conversation_summaries")
    op.drop_table("conversation_messages")
    op.drop_table("conversations")
    op.drop_table("users")
