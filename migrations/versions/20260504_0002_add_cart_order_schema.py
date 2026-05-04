"""add cart and order schema

Revision ID: 20260504_0002
Revises: 20260504_0001
Create Date: 2026-05-04 02:05:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260504_0002"
down_revision = "20260504_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "carts",
        sa.Column("id", sa.String(length=255), primary_key=True),
        sa.Column("conversation_id", sa.String(length=255), nullable=False),
        sa.Column("user_id", sa.String(length=255), nullable=False),
        sa.Column("session_id", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("total_inr", sa.Integer(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.UniqueConstraint("conversation_id", name="uq_carts_conversation"),
    )

    op.create_table(
        "cart_items",
        sa.Column("id", sa.String(length=255), primary_key=True),
        sa.Column("cart_id", sa.String(length=255), nullable=False),
        sa.Column("item_id", sa.String(length=255), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("unit_price_inr", sa.Integer(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("customizations", sa.JSON(), nullable=False),
        sa.Column("line_total_inr", sa.Integer(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["cart_id"], ["carts.id"]),
    )

    op.create_table(
        "orders",
        sa.Column("id", sa.String(length=255), primary_key=True),
        sa.Column("conversation_id", sa.String(length=255), nullable=False),
        sa.Column("user_id", sa.String(length=255), nullable=False),
        sa.Column("session_id", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("total_inr", sa.Integer(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
    )

    op.create_table(
        "order_items",
        sa.Column("id", sa.String(length=255), primary_key=True),
        sa.Column("order_id", sa.String(length=255), nullable=False),
        sa.Column("item_id", sa.String(length=255), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("unit_price_inr", sa.Integer(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("customizations", sa.JSON(), nullable=False),
        sa.Column("line_total_inr", sa.Integer(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"]),
    )

    op.create_index(
        "ix_carts_conversation_status",
        "carts",
        ["conversation_id", "status"],
    )
    op.create_index("ix_cart_items_cart_id", "cart_items", ["cart_id"])
    op.create_index(
        "ix_orders_conversation_created_at",
        "orders",
        ["conversation_id", "created_at"],
    )
    op.create_index(
        "ix_orders_session_created_at",
        "orders",
        ["session_id", "created_at"],
    )
    op.create_index("ix_order_items_order_id", "order_items", ["order_id"])


def downgrade() -> None:
    op.drop_index("ix_order_items_order_id", table_name="order_items")
    op.drop_index("ix_orders_session_created_at", table_name="orders")
    op.drop_index("ix_orders_conversation_created_at", table_name="orders")
    op.drop_index("ix_cart_items_cart_id", table_name="cart_items")
    op.drop_index("ix_carts_conversation_status", table_name="carts")
    op.drop_table("order_items")
    op.drop_table("orders")
    op.drop_table("cart_items")
    op.drop_table("carts")
