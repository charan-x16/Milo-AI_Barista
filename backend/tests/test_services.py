import pytest

from cafe.core.validator import ValidationError
from cafe.models.order import Order
from cafe.services.cart_service import add_item, view_cart
from cafe.services.faq_service import lookup_faq
from cafe.services.menu_service import get_item, search_menu
from cafe.services.order_service import cancel_order, place_order


def test_search_menu_returns_matches_and_empty_results(store):
    assert len(search_menu(store, "coffee")) >= 1
    assert search_menu(store, "xyzzy") == []


def test_get_item_returns_known_item(store):
    assert get_item(store, "m001").name == "Cappuccino"


def test_get_item_unknown_raises_validation_error(store):
    with pytest.raises(ValidationError, match="Unknown menu item: nope"):
        get_item(store, "nope")


def test_add_item_then_view_cart_shows_line_and_total(store):
    add_item(store, "s001", "m001", quantity=2)

    cart = view_cart(store, "s001")

    assert len(cart.items) == 1
    assert cart.items[0].item_id == "m001"
    assert cart.items[0].quantity == 2
    assert cart.total_inr == 360


def test_add_item_twice_merges_same_item_and_customizations(store):
    add_item(store, "s001", "m001")
    add_item(store, "s001", "m001")

    cart = view_cart(store, "s001")

    assert len(cart.items) == 1
    assert cart.items[0].quantity == 2


def test_add_item_quantity_zero_raises_validation_error(store):
    with pytest.raises(ValidationError, match="positive"):
        add_item(store, "s001", "m001", quantity=0)


def test_add_item_unknown_item_raises_validation_error(store):
    with pytest.raises(ValidationError, match="Unknown"):
        add_item(store, "s001", "nope")


def test_place_order_empty_cart_raises_validation_error(store):
    with pytest.raises(ValidationError, match="empty"):
        place_order(store, "s001")


def test_place_order_over_budget_raises_validation_error(store):
    add_item(store, "s001", "m001")

    with pytest.raises(ValidationError, match="budget"):
        place_order(store, "s001", max_budget_inr=100)


def test_place_order_success_returns_confirmed_order_and_clears_cart(store):
    add_item(store, "s001", "m001")

    order = place_order(store, "s001")

    assert isinstance(order, Order)
    assert order.status == "confirmed"
    assert order.total_inr == 180
    assert view_cart(store, "s001").is_empty() is True


def test_cancel_confirmed_order_succeeds(store):
    add_item(store, "s001", "m001")
    order = place_order(store, "s001")

    cancelled = cancel_order(store, order.order_id)

    assert cancelled.status == "cancelled"


def test_cancel_delivered_order_raises_validation_error(store):
    add_item(store, "s001", "m001")
    order = place_order(store, "s001")
    order.status = "delivered"

    with pytest.raises(ValidationError, match="delivered"):
        cancel_order(store, order.order_id)


def test_lookup_faq_hours():
    topic, answer = lookup_faq("what time do you open")

    assert topic == "hours"
    assert "7 AM" in answer


def test_lookup_faq_no_match_raises_validation_error():
    with pytest.raises(ValidationError):
        lookup_faq("blah")
