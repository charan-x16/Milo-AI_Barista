"""Wrap domain ToolResult into AgentScope's ToolResponse."""

import json

from agentscope.message import TextBlock
from agentscope.tool import ToolResponse

from cafe.models.tool_io import ToolResult


def wrap(result: ToolResult) -> ToolResponse:
    """Handle wrap.

    Args:
        - result: ToolResult - The result value.

    Returns:
        - return ToolResponse - The return value.
    """
    return ToolResponse(
        content=[
            TextBlock(
                type="text",
                text=json.dumps(result.model_dump(), ensure_ascii=False),
            )
        ]
    )
