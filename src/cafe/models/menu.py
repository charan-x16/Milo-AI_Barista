from typing import Literal

from pydantic import BaseModel, Field


Category = Literal["coffee", "tea", "food", "dessert"]


class MenuItem(BaseModel):
    id: str
    name: str
    category: Category
    price_inr: int = Field(gt=0)
    available: bool = True
    tags: list[str] = Field(default_factory=list)
