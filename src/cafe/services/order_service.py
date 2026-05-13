"""Cafe services order service module."""

import secrets

from cafe.core.validator import ValidationError
from cafe.models.order import Order
from cafe.services.cart_service import clear_cart


def place_order(store, session_id: str, max_budget_inr: int | None = None) -> Order:
    """Validates cart and budget, creates a confirmed order, clears cart, and re...

    Args:
        - store: Any - The store value.
        - session_id: str - The session id value.
        - max_budget_inr: int | None - The max budget inr value.

    Returns:
        - return Order - The return value.
    """
    cart = store.get_cart(session_id)

    if cart.is_empty():
        raise ValidationError("Your cart is empty.")

    if max_budget_inr is not None and cart.total_inr > max_budget_inr:
        raise ValidationError("Your cart total exceeds your budget.")

    order = Order(
        order_id=f"ord-{secrets.token_hex(4)}",
        session_id=session_id,
        items=list(cart.items),
        total_inr=cart.total_inr,
        status="confirmed",
    )
    store.orders[order.order_id] = order
    clear_cart(store, session_id)
    return order


def get_order(store, order_id: str) -> Order:
    """Raises if order_id unknown.

    Args:
        - store: Any - The store value.
        - order_id: str - The order id value.

    Returns:
        - return Order - The return value.
    """
    try:
        return store.orders[order_id]
    except KeyError as exc:
        raise ValidationError(f"Unknown order: {order_id}") from exc


def cancel_order(store, order_id: str) -> Order:
    """Only allowed when status in {pending, confirmed}.

    Args:
        - store: Any - The store value.
        - order_id: str - The order id value.

    Returns:
        - return Order - The return value.
    """
    order = get_order(store, order_id)

    if order.status not in {"pending", "confirmed"}:
        raise ValidationError(f"Order is {order.status} and cannot be cancelled")

    order.status = "cancelled"
    return order
