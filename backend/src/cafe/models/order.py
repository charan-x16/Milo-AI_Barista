from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field

from cafe.models.cart import CartItem


OrderStatus = Literal[
    "pending",
    "confirmed",
    "preparing",
    "ready",
    "delivered",
    "cancelled",
]


class Order(BaseModel):
    order_id: str
    session_id: str
    items: list[CartItem]
    total_inr: int
    status: OrderStatus = "pending"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
