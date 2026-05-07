from agentscope.tool import ToolResponse

from cafe.core.observability import observe_tool
from cafe.core.state import get_store
from cafe.core.validator import ValidationError
from cafe.agents.memory import (
    clear_cart_snapshot,
    resolve_menu_item_for_cart,
    save_cart_snapshot,
)
from cafe.models.tool_io import ToolResult
from cafe.services import cart_service
from cafe.tools._wrap import wrap


@observe_tool("add_to_cart")
async def add_to_cart(
    session_id: str,
    item_id: str,
    quantity: int = 1,
    customizations: list[str] | None = None,
) -> ToolResponse:
    """Add a menu item to the cart.

    Args:
        session_id: The chat session id (passed by the system).
        item_id: Menu item id (e.g. 'm001'). Get it from search_products.
        quantity: How many to add. Positive integer.
        customizations: Optional list of free-text notes.

    Returns:
        ToolResult.ok(cart=...) or ToolResult.fail(error=...).

    Example:
        add_to_cart(session_id="s1", item_id="m001", quantity=2)
    """
    try:
        try:
            cart = cart_service.add_item(
                get_store(),
                session_id,
                item_id,
                quantity,
                customizations,
            )
        except ValidationError as exc:
            if not str(exc).startswith("Unknown menu item:"):
                raise
            resolved_item = await resolve_menu_item_for_cart(item_id)
            cart = cart_service.add_resolved_item(
                get_store(),
                session_id,
                resolved_item,
                quantity,
                customizations,
            )
        await save_cart_snapshot(session_id, cart)
        return wrap(ToolResult.ok(cart=cart.model_dump(), item_count=len(cart.items), total_inr=cart.total_inr))
    except ValidationError as e:
        return wrap(ToolResult.fail(str(e)))
    except Exception as e:
        return wrap(ToolResult.fail(f"Unexpected error: {e}"))


@observe_tool("remove_from_cart")
async def remove_from_cart(session_id: str, item_id: str) -> ToolResponse:
    """Remove all units of a menu item from the cart.

    Args:
        session_id: The chat session id (passed by the system).
        item_id: Menu item id to remove.

    Returns:
        ToolResult.ok(cart=...) or ToolResult.fail(error=...).

    Example:
        remove_from_cart(session_id="s1", item_id="m001")
    """
    try:
        cart = cart_service.remove_item(get_store(), session_id, item_id)
        await save_cart_snapshot(session_id, cart)
        return wrap(ToolResult.ok(cart=cart.model_dump(), item_count=len(cart.items), total_inr=cart.total_inr))
    except ValidationError as e:
        return wrap(ToolResult.fail(str(e)))
    except Exception as e:
        return wrap(ToolResult.fail(f"Unexpected error: {e}"))


@observe_tool("view_cart")
async def view_cart(session_id: str) -> ToolResponse:
    """View the current cart.

    Args:
        session_id: The chat session id (passed by the system).

    Returns:
        ToolResult.ok(cart=...) or ToolResult.fail(error=...).

    Example:
        view_cart(session_id="s1")
    """
    try:
        cart = cart_service.view_cart(get_store(), session_id)
        await save_cart_snapshot(session_id, cart)
        return wrap(ToolResult.ok(cart=cart.model_dump(), item_count=len(cart.items), total_inr=cart.total_inr))
    except ValidationError as e:
        return wrap(ToolResult.fail(str(e)))
    except Exception as e:
        return wrap(ToolResult.fail(f"Unexpected error: {e}"))


@observe_tool("clear_cart")
async def clear_cart(session_id: str) -> ToolResponse:
    """Clear the current cart.

    Args:
        session_id: The chat session id (passed by the system).

    Returns:
        ToolResult.ok(cleared=True) or ToolResult.fail(error=...).

    Example:
        clear_cart(session_id="s1")
    """
    try:
        cart_service.clear_cart(get_store(), session_id)
        await clear_cart_snapshot(session_id)
        return wrap(ToolResult.ok(cleared=True))
    except ValidationError as e:
        return wrap(ToolResult.fail(str(e)))
    except Exception as e:
        return wrap(ToolResult.fail(f"Unexpected error: {e}"))
