"""Tests test models module."""

import pytest
from pydantic import ValidationError

from cafe.core.state import get_store, reset_store
from cafe.models.cart import Cart, CartItem
from cafe.models.menu import MenuItem
from cafe.models.tool_io import ToolResult


def test_menu_item_price_must_be_positive():
    """Verify menu item price must be positive.

    Returns:
        - return None - The return value.
    """
    with pytest.raises(ValidationError):
        MenuItem(id="m999", name="Free Sample", category="food", price_inr=0)


def test_cart_item_quantity_must_be_positive():
    """Verify cart item quantity must be positive.

    Returns:
        - return None - The return value.
    """
    with pytest.raises(ValidationError):
        CartItem(item_id="m001", name="Cappuccino", unit_price_inr=180, quantity=0)


def test_cart_total_and_is_empty_false():
    """Verify cart total and is empty false.

    Returns:
        - return None - The return value.
    """
    cart = Cart(
        session_id="s001",
        items=[
            CartItem(item_id="m001", name="Cappuccino", unit_price_inr=180, quantity=2),
            CartItem(item_id="m008", name="Brownie", unit_price_inr=140, quantity=1),
        ],
    )

    assert cart.total_inr == 500
    assert cart.is_empty() is False


def test_empty_cart_total_and_is_empty_true():
    """Verify empty cart total and is empty true.

    Returns:
        - return None - The return value.
    """
    cart = Cart(session_id="s001")

    assert cart.is_empty() is True
    assert cart.total_inr == 0


def test_tool_result_ok():
    """Verify tool result ok.

    Returns:
        - return None - The return value.
    """
    result = ToolResult.ok(x=1)

    assert result.success is True
    assert result.data["x"] == 1


def test_tool_result_fail():
    """Verify tool result fail.

    Returns:
        - return None - The return value.
    """
    result = ToolResult.fail("nope")

    assert result.success is False
    assert result.error == "nope"


def test_get_store_singleton():
    """Verify get store singleton.

    Returns:
        - return None - The return value.
    """
    assert get_store() is get_store()


def test_seeded_menu_has_eight_items(store):
    """Verify seeded menu has eight items.

    Args:
        - store: Any - The store value.

    Returns:
        - return None - The return value.
    """
    assert len(store.menu) == 8


def test_reset_store_reseeds_menu():
    """Verify reset store reseeds menu.

    Returns:
        - return None - The return value.
    """
    store = get_store()
    store.menu.clear()

    reset_store()

    assert len(get_store().menu) == 8
