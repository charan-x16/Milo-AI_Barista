from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=2000)
    enable_critic: bool = False


class ChatResponse(BaseModel):
    session_id: str
    reply: str
    tool_calls: list[dict]
    critique: dict | None
