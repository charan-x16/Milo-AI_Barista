"""Cafe models tool io module."""

from typing import Any

from pydantic import BaseModel


class ToolResult(BaseModel):
    success: bool
    data: dict[str, Any] | None = None
    error: str | None = None

    @classmethod
    def ok(cls, **data: Any) -> "ToolResult":
        """Handle ok.

        Args:
            - data: Any - The data value.

        Returns:
            - return 'ToolResult' - The return value.
        """
        return cls(success=True, data=data, error=None)

    @classmethod
    def fail(cls, error: str) -> "ToolResult":
        """Handle fail.

        Args:
            - error: str - The error value.

        Returns:
            - return 'ToolResult' - The return value.
        """
        return cls(success=False, data=None, error=error)
