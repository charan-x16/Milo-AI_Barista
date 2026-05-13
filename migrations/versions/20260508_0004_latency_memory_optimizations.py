"""latency memory optimizations

Revision ID: 20260508_0004
Revises: 20260504_0003
Create Date: 2026-05-08 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260508_0004"
down_revision = "20260504_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Apply latency-oriented memory columns and indexes."""
    op.add_column(
        "conversations",
        sa.Column(
            "last_sequence_no",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "conversations",
        sa.Column(
            "last_compressed_sequence_no",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "conversation_messages",
        sa.Column(
            "is_compressed",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.execute("""
        UPDATE conversations
        SET last_sequence_no = COALESCE((
            SELECT MAX(sequence_no)
            FROM conversation_messages
            WHERE conversation_messages.conversation_id = conversations.id
        ), 0)
        """)
    op.create_index(
        "ix_conversation_messages_visible_sequence",
        "conversation_messages",
        ["conversation_id", "visible_to_user", "sequence_no"],
    )
    op.create_index(
        "ix_conversation_messages_compressed_sequence",
        "conversation_messages",
        ["conversation_id", "is_compressed", "sequence_no"],
    )


def downgrade() -> None:
    """Revert latency-oriented memory columns and indexes."""
    op.drop_index(
        "ix_conversation_messages_compressed_sequence",
        table_name="conversation_messages",
    )
    op.drop_index(
        "ix_conversation_messages_visible_sequence",
        table_name="conversation_messages",
    )
    op.drop_column("conversation_messages", "is_compressed")
    op.drop_column("conversations", "last_compressed_sequence_no")
    op.drop_column("conversations", "last_sequence_no")
