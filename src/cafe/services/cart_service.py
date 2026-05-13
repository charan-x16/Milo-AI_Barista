"""Cafe services cart service module."""

from cafe.core.validator import ValidationError
from cafe.models.cart import Cart, CartItem
from cafe.models.menu import MenuItem
from cafe.services.menu_service import get_item


def add_resolved_item(
    store,
    session_id: str,
    item: MenuItem,
    quantity: int = 1,
    customizations: list[str] | None = None,
) -> Cart:
    """Validates a resolved menu item and adds it to the active cart.

    Args:
        - store: Any - The store value.
        - session_id: str - The session id value.
        - item: MenuItem - The item value.
        - quantity: int - The quantity value.
        - customizations: list[str] | None - The customizations value.

    Returns:
        - return Cart - The return value.
    """
    if quantity <= 0:
        raise ValidationError("Quantity must be positive.")

    if not item.available:
        raise ValidationError(f"{item.name} is currently unavailable.")

    line_customizations = customizations or []
    cart = store.get_cart(session_id)

    for line in cart.items:
        if line.item_id == item.id and line.customizations == line_customizations:
            line.quantity += quantity
            return cart

    cart.items.append(
        CartItem(
            item_id=item.id,
            name=item.name,
            unit_price_inr=item.price_inr,
            quantity=quantity,
            customizations=line_customizations,
        )
    )
    return cart


def add_item(
    store,
    session_id: str,
    item_id: str,
    quantity: int = 1,
    customizations: list[str] | None = None,
) -> Cart:
    """Validates quantity>0, item exists, item available, then adds it to cart.

    Args:
        - store: Any - The store value.
        - session_id: str - The session id value.
        - item_id: str - The item id value.
        - quantity: int - The quantity value.
        - customizations: list[str] | None - The customizations value.

    Returns:
        - return Cart - The return value.
    """
    return add_resolved_item(
        store,
        session_id,
        get_item(store, item_id),
        quantity,
        customizations,
    )


def remove_item(store, session_id: str, item_id: str) -> Cart:
    """Removes ALL units of item_id.

    Args:
        - store: Any - The store value.
        - session_id: str - The session id value.
        - item_id: str - The item id value.

    Returns:
        - return Cart - The return value.
    """
    cart = store.get_cart(session_id)
    original_count = len(cart.items)
    cart.items = [line for line in cart.items if line.item_id != item_id]

    if len(cart.items) == original_count:
        raise ValidationError(f"Item is not in cart: {item_id}")

    return cart


def view_cart(store, session_id: str) -> Cart:
    """Always succeeds.

    Args:
        - store: Any - The store value.
        - session_id: str - The session id value.

    Returns:
        - return Cart - The return value.
    """
    return store.get_cart(session_id)


def clear_cart(store, session_id: str) -> None:
    """Handle clear cart.

    Args:
        - store: Any - The store value.
        - session_id: str - The session id value.

    Returns:
        - return None - The return value.
    """
    cart = store.get_cart(session_id)
    cart.items.clear()
