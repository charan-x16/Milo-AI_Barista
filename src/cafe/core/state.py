"""Cafe core state module."""

from cafe.models.cart import Cart
from cafe.models.menu import MenuItem
from cafe.models.order import Order


class StateStore:
    def __init__(self) -> None:
        """Initialize the instance.

        Returns:
            - return None - The return value.
        """
        self.menu: dict[str, MenuItem] = {}
        self.carts: dict[str, Cart] = {}
        self.orders: dict[str, Order] = {}
        self.last_menu_scope: dict[str, str] = {}
        self.session_preferences: dict[str, set[str]] = {}

    def get_cart(self, session_id: str) -> Cart:
        """Return the cart.

        Args:
            - session_id: str - The session id value.

        Returns:
            - return Cart - The return value.
        """
        if session_id not in self.carts:
            self.carts[session_id] = Cart(session_id=session_id)
        return self.carts[session_id]

    def reset(self) -> None:
        """Handle reset.

        Returns:
            - return None - The return value.
        """
        self.carts.clear()
        self.orders.clear()
        self.last_menu_scope.clear()
        self.session_preferences.clear()


def _seed_menu(store: StateStore) -> None:
    """Handle seed menu.

    Args:
        - store: StateStore - The store value.

    Returns:
        - return None - The return value.
    """
    items = [
        MenuItem(
            id="m001",
            name="Cappuccino",
            category="coffee",
            price_inr=180,
            tags=["hot", "milk"],
        ),
        MenuItem(
            id="m002",
            name="Latte",
            category="coffee",
            price_inr=200,
            tags=["hot", "milk"],
        ),
        MenuItem(
            id="m003",
            name="Espresso",
            category="coffee",
            price_inr=120,
            tags=["hot", "strong"],
        ),
        MenuItem(
            id="m004",
            name="Cold Coffee",
            category="coffee",
            price_inr=190,
            tags=["cold", "milk"],
        ),
        MenuItem(
            id="m005",
            name="Masala Chai",
            category="tea",
            price_inr=80,
            tags=["hot", "spiced"],
        ),
        MenuItem(
            id="m006",
            name="Croissant",
            category="food",
            price_inr=150,
            tags=["vegetarian", "baked"],
        ),
        MenuItem(
            id="m007",
            name="Veg Sandwich",
            category="food",
            price_inr=220,
            tags=["vegetarian", "savoury"],
        ),
        MenuItem(
            id="m008",
            name="Brownie",
            category="dessert",
            price_inr=140,
            tags=["chocolate", "vegetarian"],
        ),
    ]
    store.menu = {item.id: item for item in items}


_store = StateStore()
_seed_menu(_store)


def get_store() -> StateStore:
    """Return the store.

    Returns:
        - return StateStore - The return value.
    """
    return _store


def reset_store() -> None:
    """Reset the store.

    Returns:
        - return None - The return value.
    """
    _store.reset()
    _seed_menu(_store)
