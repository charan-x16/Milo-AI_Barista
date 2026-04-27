import pytest
from pydantic import ValidationError

from cafe.core.state import get_store, reset_store
from cafe.models.cart import Cart, CartItem
from cafe.models.menu import MenuItem
from cafe.models.tool_io import ToolResult


def test_menu_item_price_must_be_positive():
    with pytest.raises(ValidationError):
        MenuItem(id="m999", name="Free Sample", category="food", price_inr=0)


def test_cart_item_quantity_must_be_positive():
    with pytest.raises(ValidationError):
        CartItem(item_id="m001", name="Cappuccino", unit_price_inr=180, quantity=0)


def test_cart_total_and_is_empty_false():
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
    cart = Cart(session_id="s001")

    assert cart.is_empty() is True
    assert cart.total_inr == 0


def test_tool_result_ok():
    result = ToolResult.ok(x=1)

    assert result.success is True
    assert result.data["x"] == 1


def test_tool_result_fail():
    result = ToolResult.fail("nope")

    assert result.success is False
    assert result.error == "nope"


def test_get_store_singleton():
    assert get_store() is get_store()


def test_seeded_menu_has_eight_items(store):
    assert len(store.menu) == 8


def test_reset_store_reseeds_menu():
    store = get_store()
    store.menu.clear()

    reset_store()

    assert len(get_store().menu) == 8
