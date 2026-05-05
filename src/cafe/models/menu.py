from pydantic import BaseModel, Field


class MenuItem(BaseModel):
    id: str
    name: str
    category: str
    price_inr: int = Field(gt=0)
    available: bool = True
    tags: list[str] = Field(default_factory=list)
