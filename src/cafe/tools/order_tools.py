from agentscope.tool import ToolResponse

from cafe.core.state import get_store
from cafe.core.validator import ValidationError
from cafe.models.tool_io import ToolResult
from cafe.services import order_service
from cafe.tools._wrap import wrap


async def place_order(session_id: str, max_budget_inr: int | None = None) -> ToolResponse:
    """Place an order from the current cart.

    Args:
        session_id: The chat session id (passed by the system).
        max_budget_inr: Optional maximum budget in INR.

    Returns:
        ToolResult.ok(order=...) or ToolResult.fail(error=...).

    Example:
        place_order(session_id="s1", max_budget_inr=500)
    """
    try:
        order = order_service.place_order(get_store(), session_id, max_budget_inr)
        return wrap(ToolResult.ok(order=order.model_dump(mode="json")))
    except ValidationError as e:
        return wrap(ToolResult.fail(str(e)))
    except Exception as e:
        return wrap(ToolResult.fail(f"Unexpected error: {e}"))


async def track_order(order_id: str) -> ToolResponse:
    """Get the current status and details for an order.

    Args:
        order_id: Order id returned by place_order.

    Returns:
        ToolResult.ok(order=...) or ToolResult.fail(error=...).

    Example:
        track_order(order_id="ord-1234abcd")
    """
    try:
        order = order_service.get_order(get_store(), order_id)
        return wrap(ToolResult.ok(order=order.model_dump(mode="json")))
    except ValidationError as e:
        return wrap(ToolResult.fail(str(e)))
    except Exception as e:
        return wrap(ToolResult.fail(f"Unexpected error: {e}"))


async def cancel_order(order_id: str) -> ToolResponse:
    """Cancel a pending or confirmed order.

    Args:
        order_id: Order id returned by place_order.

    Returns:
        ToolResult.ok(order=...) or ToolResult.fail(error=...).

    Example:
        cancel_order(order_id="ord-1234abcd")
    """
    try:
        order = order_service.cancel_order(get_store(), order_id)
        return wrap(ToolResult.ok(order=order.model_dump(mode="json")))
    except ValidationError as e:
        return wrap(ToolResult.fail(str(e)))
    except Exception as e:
        return wrap(ToolResult.fail(f"Unexpected error: {e}"))
