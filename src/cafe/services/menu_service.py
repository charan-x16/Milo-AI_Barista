"""Cafe services menu service module."""

from cafe.core.validator import ValidationError
from cafe.models.menu import MenuItem


def search_menu(store, query: str, max_results: int = 5) -> list[MenuItem]:
    """Case-insensitive substring match across name, category, tags.

    Args:
        - store: Any - The store value.
        - query: str - The query value.
        - max_results: int - The max results value.

    Returns:
        - return list[MenuItem] - The return value.
    """
    normalized_query = query.casefold()
    results: list[MenuItem] = []

    for item in store.menu.values():
        searchable = [item.name, item.category, *item.tags]
        if any(normalized_query in value.casefold() for value in searchable):
            results.append(item)
            if len(results) >= max_results:
                break

    return results


def get_item(store, item_id: str) -> MenuItem:
    """Raises ValidationError('Unknown menu item: {id}') if missing.

    Args:
        - store: Any - The store value.
        - item_id: str - The item id value.

    Returns:
        - return MenuItem - The return value.
    """
    try:
        return store.menu[item_id]
    except KeyError as exc:
        raise ValidationError(f"Unknown menu item: {item_id}") from exc
