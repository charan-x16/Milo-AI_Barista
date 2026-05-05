"""add menu items catalog

Revision ID: 20260504_0003
Revises: 20260504_0002
Create Date: 2026-05-04 02:25:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260504_0003"
down_revision = "20260504_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "menu_items",
        sa.Column("id", sa.String(length=255), primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("normalized_name", sa.String(length=255), nullable=False),
        sa.Column("top_level", sa.String(length=255), nullable=False),
        sa.Column("section", sa.String(length=255), nullable=False),
        sa.Column("price_inr", sa.Integer(), nullable=False),
        sa.Column("serving", sa.String(length=255), nullable=True),
        sa.Column("dietary_tags", sa.String(length=255), nullable=True),
        sa.Column("tags", sa.JSON(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("available", sa.Boolean(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint(
            "normalized_name",
            name="uq_menu_items_normalized_name",
        ),
    )
    op.create_index("ix_menu_items_name", "menu_items", ["name"])
    op.create_index(
        "ix_menu_items_section",
        "menu_items",
        ["top_level", "section"],
    )


def downgrade() -> None:
    op.drop_index("ix_menu_items_section", table_name="menu_items")
    op.drop_index("ix_menu_items_name", table_name="menu_items")
    op.drop_table("menu_items")
