"""Cafe api schemas module."""

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    user_id: str = Field(default="anonymous", min_length=1, max_length=128)
    session_id: str = Field(min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=2000)
    enable_critic: bool = False


class ChatResponse(BaseModel):
    request_id: str
    user_id: str = "anonymous"
    session_id: str
    reply: str
    tool_calls: list[dict]
    critique: dict | None
