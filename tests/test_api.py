"""Tests test api module."""

import asyncio
import os

import pytest
from agentscope.message import Msg
from fastapi.testclient import TestClient

from cafe.agents.memory import load_memory
from cafe.api.main import app
from cafe.core.state import get_store
from cafe.services.cart_service import add_item


@pytest.fixture
def client():
    """Verify client.

    Returns:
        - return Any - The return value.
    """
    return TestClient(app)


@pytest.fixture(autouse=True)
def reset(client):
    """Verify reset.

    Args:
        - client: Any - The client value.

    Returns:
        - return None - The return value.
    """
    client.post("/admin/reset")
    yield


def test_health(client):
    """Verify health.

    Args:
        - client: Any - The client value.

    Returns:
        - return None - The return value.
    """
    assert client.get("/health").json()["status"] == "ok"


def test_menu_eight(client):
    """Verify menu eight.

    Args:
        - client: Any - The client value.

    Returns:
        - return None - The return value.
    """
    assert len(client.get("/menu").json()["items"]) == 8


def test_new_session(client):
    """Verify new session.

    Args:
        - client: Any - The client value.

    Returns:
        - return None - The return value.
    """
    assert "session_id" in client.post("/sessions").json()


def test_empty_cart(client):
    """Verify empty cart.

    Args:
        - client: Any - The client value.

    Returns:
        - return None - The return value.
    """
    assert client.get("/sessions/abc/cart").json()["total_inr"] == 0


def test_reset_session_clears_current_cart(client):
    """Verify reset session clears current cart.

    Args:
        - client: Any - The client value.

    Returns:
        - return None - The return value.
    """
    add_item(get_store(), "abc", "m001", quantity=2)

    assert client.get("/sessions/abc/cart").json()["total_inr"] == 360

    response = client.post("/sessions/abc/reset")

    assert response.status_code == 200
    assert client.get("/sessions/abc/cart").json()["total_inr"] == 0


def test_conversation_history_apis_return_sql_memory(client):
    """Verify conversation history apis return sql memory.

    Args:
        - client: Any - The client value.

    Returns:
        - return None - The return value.
    """

    async def seed_memory():
        """Verify seed memory.

        Returns:
            - return None - The return value.
        """
        memory = load_memory("history-session", user_id="anonymous")
        await memory.clear()
        await memory.add(
            [
                Msg(
                    "user",
                    "[session_id=history-session] show menu",
                    "user",
                    metadata={"display_text": "show menu"},
                ),
                Msg("assistant", "Of course. Here are the menu sections.", "assistant"),
            ]
        )

    asyncio.run(seed_memory())

    conversations = client.get("/users/anonymous/conversations").json()
    messages = client.get("/sessions/history-session/messages").json()

    assert conversations["user_id"] == "anonymous"
    assert conversations["conversations"][0]["session_id"] == "history-session"
    assert conversations["conversations"][0]["title"] == "show menu"
    assert conversations["conversations"][0]["last_message"] == (
        "Of course. Here are the menu sections."
    )
    assert [msg["role"] for msg in messages["messages"]] == ["user", "assistant"]
    assert messages["messages"][0]["content"] == "show menu"
    assert messages["messages"][1]["content"] == (
        "Of course. Here are the menu sections."
    )


def test_chat_validation(client):
    """Verify chat validation.

    Args:
        - client: Any - The client value.

    Returns:
        - return None - The return value.
    """
    response = client.post("/chat", json={"session_id": "", "message": "hi"})

    assert response.status_code == 422


def test_chat_schedules_compression_without_exposing_internal_flag(client, monkeypatch):
    """Verify /chat schedules compression and hides internal fields.

    Args:
        - client: Any - The test client.
        - monkeypatch: Any - The monkeypatch value.

    Returns:
        - return None - This test has no return value.
    """
    from cafe.api import main

    called = []

    async def fake_run_turn(*_args, **_kwargs):
        """Return a response that needs background compression.

        Args:
            - _args: Any - The args value.
            - _kwargs: Any - The kwargs value.

        Returns:
            - return dict[str, Any] - The fake turn output.
        """
        return {
            "request_id": "req-1",
            "reply": "Agent reply.",
            "tool_calls": [],
            "critique": None,
            "needs_compression": True,
        }

    async def fake_compression_job(*, user_id, session_id):
        """Record background compression execution.

        Args:
            - user_id: Any - The user id value.
            - session_id: Any - The session id value.

        Returns:
            - return None - This test helper has no return value.
        """
        called.append((user_id, session_id))

    monkeypatch.setattr(main, "run_turn", fake_run_turn)
    monkeypatch.setattr(main, "run_memory_compression_job", fake_compression_job)

    response = client.post(
        "/chat",
        json={"session_id": "api-bg", "user_id": "u1", "message": "complex"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["request_id"] == "req-1"
    assert "needs_compression" not in body
    assert called == [("u1", "api-bg")]


@pytest.mark.skipif(
    not (os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY")),
    reason="needs LLM_API_KEY or OPENAI_API_KEY",
)
def test_chat_end_to_end(client):
    """Verify chat end to end.

    Args:
        - client: Any - The client value.

    Returns:
        - return None - The return value.
    """
    sid = client.post("/sessions").json()["session_id"]
    response = client.post(
        "/chat",
        json={
            "session_id": sid,
            "message": "Add a masala chai and place the order, budget ₹100.",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["reply"]
    orders = client.get(f"/sessions/{sid}/orders").json()["orders"]
    assert len(orders) >= 1
