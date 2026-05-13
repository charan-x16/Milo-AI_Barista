"""Cafe models cart module."""

from pydantic import BaseModel, Field, computed_field


class CartItem(BaseModel):
    item_id: str
    name: str
    unit_price_inr: int
    quantity: int = Field(gt=0)
    customizations: list[str] = Field(default_factory=list)

    @computed_field
    @property
    def line_total_inr(self) -> int:
        """Handle line total inr.

        Returns:
            - return int - The return value.
        """
        return self.unit_price_inr * self.quantity


class Cart(BaseModel):
    session_id: str
    items: list[CartItem] = Field(default_factory=list)

    @computed_field
    @property
    def total_inr(self) -> int:
        """Handle total inr.

        Returns:
            - return int - The return value.
        """
        return sum(item.line_total_inr for item in self.items)

    def is_empty(self) -> bool:
        """Return whether empty.

        Returns:
            - return bool - The return value.
        """
        return len(self.items) == 0
