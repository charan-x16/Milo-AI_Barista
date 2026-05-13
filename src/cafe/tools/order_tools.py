"""Cafe tools order tools module."""

from agentscope.tool import ToolResponse

from cafe.agents.memory import clear_cart_snapshot, save_order_snapshot
from cafe.core.observability import observe_tool
from cafe.core.state import get_store
from cafe.core.validator import ValidationError
from cafe.models.tool_io import ToolResult
from cafe.services import order_service
from cafe.tools._wrap import wrap


@observe_tool("place_order")
async def place_order(
    session_id: str, max_budget_inr: int | None = None
) -> ToolResponse:
    """Place an order from the current cart.

    Args:
        - session_id: str - The session id value.
        - max_budget_inr: int | None - The max budget inr value.

    Returns:
        - return ToolResponse - The return value.
    """
    try:
        order = order_service.place_order(get_store(), session_id, max_budget_inr)
        await save_order_snapshot(order)
        await clear_cart_snapshot(session_id)
        return wrap(ToolResult.ok(order=order.model_dump(mode="json")))
    except ValidationError as e:
        return wrap(ToolResult.fail(str(e)))
    except Exception as e:
        return wrap(ToolResult.fail(f"Unexpected error: {e}"))


@observe_tool("track_order")
async def track_order(order_id: str) -> ToolResponse:
    """Get the current status and details for an order.

    Args:
        - order_id: str - The order id value.

    Returns:
        - return ToolResponse - The return value.
    """
    try:
        order = order_service.get_order(get_store(), order_id)
        await save_order_snapshot(order)
        return wrap(ToolResult.ok(order=order.model_dump(mode="json")))
    except ValidationError as e:
        return wrap(ToolResult.fail(str(e)))
    except Exception as e:
        return wrap(ToolResult.fail(f"Unexpected error: {e}"))


@observe_tool("cancel_order")
async def cancel_order(order_id: str) -> ToolResponse:
    """Cancel a pending or confirmed order.

    Args:
        - order_id: str - The order id value.

    Returns:
        - return ToolResponse - The return value.
    """
    try:
        order = order_service.cancel_order(get_store(), order_id)
        await save_order_snapshot(order)
        return wrap(ToolResult.ok(order=order.model_dump(mode="json")))
    except ValidationError as e:
        return wrap(ToolResult.fail(str(e)))
    except Exception as e:
        return wrap(ToolResult.fail(f"Unexpected error: {e}"))
